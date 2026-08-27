from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .db import Database
from .normalizer import _record_id, normalize_records, normalize_snapshot
from .repository import SnapshotRepository
from .source import SnapshotSource


class SyncService:
    def __init__(self, database: Database, source: SnapshotSource, settings: Settings):
        self.database = database
        self.source = source
        self.settings = settings
        self.records = SnapshotRepository(database)

    def create_job(self) -> dict[str, Any]:
        now = self._now()
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT job_id FROM sync_jobs WHERE status IN ('queued', 'running') LIMIT 1"
            ).fetchone()
            if existing:
                raise RuntimeError("a synchronization job is already running")
            job_id = "sync_" + uuid.uuid4().hex[:16]
            connection.execute(
                "INSERT INTO sync_jobs(job_id, status, created_at) VALUES (?, 'queued', ?)",
                (job_id, now),
            )
            connection.commit()
        return self.get_job(job_id)

    async def run_job(self, job_id: str) -> None:
        self._update_job(job_id, status="running", started_at=self._now())
        try:
            payload = await self.source.fetch()
            snapshot, sync_mode = self._build_snapshot(payload)
            self.database.save_snapshot(snapshot)
            self._update_job(
                job_id,
                status="succeeded",
                completed_at=self._now(),
                message=f"{sync_mode} synchronization completed",
                validation=snapshot["validation"],
            )
        except Exception as exc:  # The failed job is persisted; old snapshot remains active.
            status = "auth_required" if "401" in str(exc) or "403" in str(exc) else "failed"
            self._update_job(
                job_id,
                status=status,
                completed_at=self._now(),
                message=str(exc),
            )

    def import_manual_records(
        self, *, data_type: str, records: list[dict[str, Any]], filename: str
    ) -> dict[str, Any]:
        """Import one user-uploaded CRM dataset without touching manual project fields."""
        now = self._now()
        active = self.records.export_records()
        normalized_incoming, _ = normalize_records(
            {"updated_at": now, "records": records},
            record_id_secret=self.settings.record_id_secret,
            timezone=self.settings.timezone,
        )
        active_by_id = {row["record_id"]: row for row in active["records"]} if active else {}
        imported_changes = {"inserted_records": 0, "updated_records": 0, "unchanged_records": 0}
        prepared_records: list[dict[str, Any]] = []
        for source_record, record in zip(records, normalized_incoming, strict=True):
            previous = active_by_id.get(record["record_id"])
            if previous is None and source_record.get("crm_internal_id"):
                # Compatibility with snapshots created before CRM IDs were
                # type-scoped. Never match a legacy key across data types.
                legacy_id = _record_id(
                    str(source_record["crm_internal_id"]),
                    secret=self.settings.record_id_secret,
                )
                legacy = active_by_id.get(legacy_id)
                if legacy and legacy["data_type"] == record["data_type"]:
                    previous = legacy
                    record = {**record, "record_id": legacy["record_id"]}
            present_fields = set(source_record.get("__present_fields") or ())
            if previous:
                record = _preserve_missing_upload_fields(record, previous, present_fields)
            if previous is None:
                imported_changes["inserted_records"] += 1
            else:
                if previous.get("merged_from_opportunity") and record["data_type"] == "contract_order":
                    record = {**record, "probability": 100, "merged_from_opportunity": True}
                if _record_business_data(previous) == _record_business_data(record):
                    imported_changes["unchanged_records"] += 1
                else:
                    imported_changes["updated_records"] += 1
            prepared_records.append(record)
        snapshot, mode = self._build_snapshot(
            {
                "sync_mode": "incremental",
                "updated_at": now,
                "records": prepared_records,
            }
        )
        snapshot["validation"].update(
            {
                **imported_changes,
                "import_kind": "manual_upload",
                "import_data_type": data_type,
                "import_filename": filename,
            }
        )
        snapshot_id = self.database.save_snapshot(snapshot)
        import_log = self.database.save_crm_import_log(
            snapshot_id=snapshot_id,
            data_type=data_type,
            filename=filename,
            parsed_rows=len(records),
            inserted_records=imported_changes["inserted_records"],
            updated_records=imported_changes["updated_records"],
            unchanged_records=imported_changes["unchanged_records"],
            imported_at=snapshot["imported_at"],
        )
        return {
            "mode": mode,
            "imported_at": snapshot["imported_at"],
            "validation": snapshot["validation"],
            "import_log": import_log,
        }

    def _build_snapshot(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        active = self.records.export_records()
        requested_mode = str(payload.get("sync_mode") or "auto").lower()
        if requested_mode not in {"auto", "full", "incremental", "delta"}:
            raise ValueError("sync_mode must be auto, full, incremental, or delta")
        mode = (
            "full"
            if requested_mode == "full" or active is None
            else "incremental"
        )
        if mode == "full":
            snapshot = normalize_snapshot(
                payload,
                record_id_secret=self.settings.record_id_secret,
                timezone=self.settings.timezone,
            )
            snapshot["validation"]["sync_mode"] = "full"
            snapshot["validation"]["inserted_records"] = len(snapshot["records"])
            snapshot["validation"]["updated_records"] = 0
            snapshot["validation"]["deleted_records"] = 0
            snapshot["validation"]["unchanged_records"] = 0
            return snapshot, "full"

        incoming, updated_at = normalize_records(
            payload,
            record_id_secret=self.settings.record_id_secret,
            timezone=self.settings.timezone,
        )
        existing_by_id = {row["record_id"]: row for row in active["records"]}
        deleted_ids = set(payload.get("deleted_record_ids") or [])
        for record_id in deleted_ids:
            existing_by_id.pop(record_id, None)
        for record in incoming:
            previous = existing_by_id.get(record["record_id"])
            # Converted opportunities are intentionally hidden from the active
            # project view. Preserve their success marker while a later manual
            # contract upload updates the same project.
            if (
                previous
                and previous.get("merged_from_opportunity")
                and record["data_type"] == "contract_order"
            ):
                record["probability"] = 100
                record["merged_from_opportunity"] = True
            existing_by_id[record["record_id"]] = record

        combined_payload = {
            "schema_version": payload.get("schema_version", "2.2"),
            "updated_at": updated_at,
            "records": list(existing_by_id.values()),
        }
        snapshot = normalize_snapshot(
            combined_payload,
            record_id_secret=self.settings.record_id_secret,
            timezone=self.settings.timezone,
        )
        old_final = {row["record_id"]: row for row in active["records"]}
        inserted = updated = unchanged = 0
        for record in snapshot["records"]:
            old = old_final.get(record["record_id"])
            if old is None:
                inserted += 1
            elif _record_business_data(old) == _record_business_data(record):
                unchanged += 1
                record["updated_at"] = old["updated_at"]
            else:
                updated += 1
        deleted = len(set(old_final) - {row["record_id"] for row in snapshot["records"]})
        snapshot["validation"].update(
            {
                "sync_mode": "incremental",
                "received_delta_records": len(incoming),
                "inserted_records": inserted,
                "updated_records": updated,
                "deleted_records": deleted,
                "unchanged_records": unchanged,
            }
        )
        return snapshot, "incremental"

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "job_id": row["job_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "message": row["message"],
            "validation": json.loads(row["validation_json"])
            if row["validation_json"]
            else None,
        }

    async def scheduler_loop(self) -> None:
        while True:
            now = datetime.now(ZoneInfo(self.settings.timezone))
            next_run = now.replace(
                hour=self.settings.sync_hour, minute=0, second=0, microsecond=0
            )
            if next_run <= now:
                next_run += timedelta(days=1)
            await asyncio.sleep((next_run - now).total_seconds())
            try:
                job = self.create_job()
            except RuntimeError:
                continue
            await self.run_job(job["job_id"])

    def _update_job(
        self,
        job_id: str,
        *,
        status: str,
        started_at: str | None = None,
        completed_at: str | None = None,
        message: str | None = None,
        validation: dict[str, Any] | None = None,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE sync_jobs
                SET status = ?,
                    started_at = COALESCE(?, started_at),
                    completed_at = COALESCE(?, completed_at),
                    message = COALESCE(?, message),
                    validation_json = COALESCE(?, validation_json)
                WHERE job_id = ?
                """,
                (
                    status,
                    started_at,
                    completed_at,
                    message,
                    json.dumps(validation, ensure_ascii=False) if validation else None,
                    job_id,
                ),
            )
            connection.commit()

    def _now(self) -> str:
        return datetime.now(ZoneInfo(self.settings.timezone)).isoformat(timespec="seconds")


def _record_business_data(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in {"updated_at", "merged_from_opportunity"}
    }


def _preserve_missing_upload_fields(
    record: dict[str, Any], previous: dict[str, Any], present_fields: set[str]
) -> dict[str, Any]:
    """A template may omit columns; omitted columns are not blanking operations."""
    field_map = {
        "project_number": "project_number",
        "subject": "subject",
        "customer": "customer",
        "category": "category",
        "signed_at": "signed_at",
        "total_amount": "total_amount",
        "received_amount": "received_amount",
        "invoiced_amount": "invoiced_amount",
        "owner": "owner",
        "probability": "probability",
        "status": "status",
    }
    merged = dict(record)
    for source_field, record_field in field_map.items():
        if source_field not in present_fields:
            merged[record_field] = previous.get(record_field)
    return merged
