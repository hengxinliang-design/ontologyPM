from __future__ import annotations

import json
import math
from typing import Any

from .db import Database


SORT_COLUMNS = {
    "project_number": "project_number",
    "subject": "subject",
    "signed_at": "signed_at",
    "owner": "owner",
    "probability": "probability",
    "status": "status",
}


class SnapshotRepository:
    def __init__(self, database: Database):
        self.database = database

    def list_records(
        self,
        *,
        data_type: str | None,
        project_number: str | None,
        owner: str | None,
        status: str | None,
        probability: int | None,
        query: str | None,
        signed_from: str | None,
        signed_to: str | None,
        page: int,
        page_size: int,
        sort: str,
    ) -> dict[str, Any] | None:
        snapshot_id = self.database.active_snapshot_id()
        if snapshot_id is None:
            return None
        clauses = ["snapshot_id = ?"]
        params: list[Any] = [snapshot_id]
        for column, value in (
            ("data_type", data_type),
            ("project_number", project_number),
            ("owner", owner),
            ("status", status),
            ("probability", probability),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if query:
            clauses.append("(subject LIKE ? OR customer LIKE ? OR project_number LIKE ?)")
            needle = f"%{query}%"
            params.extend([needle, needle, needle])
        if signed_from:
            clauses.append("signed_at >= ?")
            params.append(signed_from)
        if signed_to:
            clauses.append("signed_at <= ?")
            params.append(signed_to)

        where = " AND ".join(clauses)
        descending = sort.startswith("-")
        sort_key = sort.removeprefix("-")
        sort_column = SORT_COLUMNS.get(sort_key, "project_number")
        direction = "DESC" if descending else "ASC"
        offset = (page - 1) * page_size
        with self.database.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS count FROM records WHERE {where}", params
            ).fetchone()["count"]
            rows = connection.execute(
                f"""
                SELECT * FROM records WHERE {where}
                ORDER BY {sort_column} {direction}, id ASC
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, offset],
            ).fetchall()
            snapshot = connection.execute(
                "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        return {
            "records": [_serialize_record(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total_records": total,
            "total_pages": math.ceil(total / page_size) if total else 0,
            "schema_version": snapshot["schema_version"],
            "updated_at": snapshot["source_updated_at"],
        }

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        snapshot_id = self.database.active_snapshot_id()
        if snapshot_id is None:
            return None
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM records WHERE snapshot_id = ? AND record_id = ?",
                (snapshot_id, record_id),
            ).fetchone()
        return _serialize_record(row) if row else None

    def get_project(self, project_number: str) -> list[dict[str, Any]] | None:
        snapshot_id = self.database.active_snapshot_id()
        if snapshot_id is None:
            return None
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM records
                WHERE snapshot_id = ? AND project_number = ?
                ORDER BY data_type, signed_at, id
                """,
                (snapshot_id, project_number),
            ).fetchall()
        return [_serialize_record(row) for row in rows]

    def sync_status(self) -> dict[str, Any]:
        snapshot_id = self.database.active_snapshot_id()
        with self.database.connect() as connection:
            running = connection.execute(
                """
                SELECT * FROM sync_jobs WHERE status IN ('queued', 'running')
                ORDER BY created_at DESC LIMIT 1
                """
            ).fetchone()
            if snapshot_id is None:
                return {
                    "status": running["status"] if running else "never_run",
                    "last_success_at": None,
                    "source_stats": {"contract_order": 0, "sales_opportunity": 0},
                    "output_stats": {"contract_order": 0, "sales_opportunity": 0},
                    "validation": _empty_validation(),
                }
            snapshot = connection.execute(
                "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        return {
            "status": running["status"] if running else "succeeded",
            "last_success_at": snapshot["imported_at"],
            "source_stats": {
                "contract_order": snapshot["source_contract_count"],
                "sales_opportunity": snapshot["source_opportunity_count"],
            },
            "output_stats": {
                "contract_order": snapshot["output_contract_count"],
                "sales_opportunity": snapshot["output_opportunity_count"],
            },
            "validation": json.loads(snapshot["validation_json"]),
        }

    def export_records(self) -> dict[str, Any] | None:
        snapshot_id = self.database.active_snapshot_id()
        if snapshot_id is None:
            return None
        with self.database.connect() as connection:
            snapshot = connection.execute(
                "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
            rows = connection.execute(
                "SELECT * FROM records WHERE snapshot_id = ? ORDER BY id", (snapshot_id,)
            ).fetchall()
        return {
            "schema_version": snapshot["schema_version"],
            "updated_at": snapshot["source_updated_at"],
            "summary": {
                "total_records": len(rows),
                "by_type": {
                    "contract_order": snapshot["output_contract_count"],
                    "sales_opportunity": snapshot["output_opportunity_count"],
                },
            },
            "records": [_serialize_record(row) for row in rows],
        }


def _serialize_record(row: Any) -> dict[str, Any]:
    return {
        "record_id": row["record_id"],
        "data_type": row["data_type"],
        "project_number": row["project_number"],
        "subject": row["subject"],
        "customer": row["customer"],
        "category": row["category"],
        "signed_at": row["signed_at"],
        "total_amount": _money(row["total_amount"], row["total_amount_display"]),
        "received_amount": _money(
            row["received_amount"], row["received_amount_display"]
        ),
        "invoiced_amount": _money(
            row["invoiced_amount"], row["invoiced_amount_display"]
        ),
        "owner": row["owner"],
        "probability": row["probability"],
        "status": row["status"],
        "merged_from_opportunity": bool(row["merged_from_opportunity"]),
        "updated_at": row["updated_at"],
    }


def _money(amount: str | None, display: str | None) -> dict[str, str] | None:
    if amount is None:
        return None
    return {"amount": amount, "currency": "CNY", "display": display or amount}


def _empty_validation() -> dict[str, Any]:
    return {
        "complete": False,
        "missing_required_values": 0,
        "duplicate_internal_ids": 0,
        "cross_type_duplicate_project_ids": 0,
        "merged_opportunities": 0,
    }
