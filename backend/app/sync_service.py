from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .db import Database
from .normalizer import normalize_records, normalize_snapshot
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
