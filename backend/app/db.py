from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    source_contract_count INTEGER NOT NULL,
    source_opportunity_count INTEGER NOT NULL,
    output_contract_count INTEGER NOT NULL,
    output_opportunity_count INTEGER NOT NULL,
    merged_opportunity_count INTEGER NOT NULL,
    validation_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    record_id TEXT NOT NULL,
    data_type TEXT NOT NULL CHECK (data_type IN ('contract_order', 'sales_opportunity')),
    project_number TEXT,
    subject TEXT NOT NULL,
    customer TEXT,
    category TEXT,
    signed_at TEXT,
    total_amount TEXT,
    total_amount_display TEXT,
    received_amount TEXT,
    received_amount_display TEXT,
    invoiced_amount TEXT,
    invoiced_amount_display TEXT,
    owner TEXT NOT NULL,
    probability INTEGER CHECK (probability IS NULL OR probability BETWEEN 0 AND 100),
    status TEXT,
    merged_from_opportunity INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(snapshot_id, record_id)
);

CREATE INDEX IF NOT EXISTS idx_records_snapshot ON records(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_records_project ON records(snapshot_id, project_number);
CREATE INDEX IF NOT EXISTS idx_records_type ON records(snapshot_id, data_type);
CREATE INDEX IF NOT EXISTS idx_records_owner ON records(snapshot_id, owner);
CREATE INDEX IF NOT EXISTS idx_records_status ON records(snapshot_id, status);

CREATE TABLE IF NOT EXISTS sync_jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    message TEXT,
    validation_json TEXT
);

CREATE TABLE IF NOT EXISTS crm_import_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    data_type TEXT NOT NULL CHECK (data_type IN ('contract_order', 'sales_opportunity')),
    filename TEXT NOT NULL,
    parsed_rows INTEGER NOT NULL,
    inserted_records INTEGER NOT NULL DEFAULT 0,
    updated_records INTEGER NOT NULL DEFAULT 0,
    unchanged_records INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_crm_import_logs_imported_at
    ON crm_import_logs(imported_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS service_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_settings (
    project_number TEXT PRIMARY KEY,
    contract_type TEXT NOT NULL DEFAULT 'unknown'
        CHECK (contract_type IN ('construction', 'maintenance', 'open_ended', 'sales', 'unknown')),
    lifecycle_status TEXT NOT NULL DEFAULT 'planned'
        CHECK (lifecycle_status IN ('planned', 'active', 'on_hold', 'completed', 'closed')),
    planned_person_days TEXT,
    hours_per_person_day TEXT NOT NULL DEFAULT '8.00',
    planned_start TEXT,
    planned_end TEXT,
    project_manager TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS time_entries (
    external_id TEXT PRIMARY KEY,
    project_number TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    employee_name TEXT NOT NULL,
    work_date TEXT NOT NULL,
    hours TEXT NOT NULL,
    approved_hours TEXT NOT NULL,
    approval_status TEXT NOT NULL
        CHECK (approval_status IN ('draft', 'submitted', 'approved', 'rejected')),
    work_content TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_time_project_date
    ON time_entries(project_number, work_date DESC);

CREATE INDEX IF NOT EXISTS idx_time_employee_date
    ON time_entries(employee_name, work_date DESC);

CREATE TABLE IF NOT EXISTS worktime_baseline_config (
    config_id TEXT PRIMARY KEY DEFAULT 'default',
    standard_work_days_per_year TEXT NOT NULL DEFAULT '230.00',
    standard_hours_per_day TEXT NOT NULL DEFAULT '8.00',
    annual_baseline_hours TEXT NOT NULL DEFAULT '1840.00',
    effective_baseline_hours TEXT NOT NULL DEFAULT '1564.00',
    monthly_baseline_hours TEXT NOT NULL DEFAULT '153.00',
    quarterly_baseline_hours TEXT NOT NULL DEFAULT '460.00',
    minimum_utilization_percent TEXT NOT NULL DEFAULT '75.00',
    excellent_utilization_percent TEXT NOT NULL DEFAULT '90.00',
    attention_utilization_percent TEXT NOT NULL DEFAULT '60.00',
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS milestones (
    milestone_id TEXT PRIMARY KEY,
    project_number TEXT NOT NULL,
    sequence_no INTEGER,
    name TEXT NOT NULL,
    planned_date TEXT NOT NULL,
    planned_start TEXT,
    planned_end TEXT,
    completed_date TEXT,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'in_progress', 'completed', 'blocked')),
    owner TEXT,
    deliverable TEXT,
    is_payment_node INTEGER NOT NULL DEFAULT 0,
    payment_node_id TEXT,
    confirmed_by TEXT,
    confirmed_at TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_milestone_project
    ON milestones(project_number, planned_date);

CREATE TABLE IF NOT EXISTS payment_nodes (
    payment_node_id TEXT PRIMARY KEY,
    project_number TEXT NOT NULL,
    name TEXT NOT NULL,
    planned_date TEXT NOT NULL,
    planned_amount TEXT NOT NULL,
    actual_date TEXT,
    actual_amount TEXT NOT NULL DEFAULT '0.00',
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'invoiced', 'partially_paid', 'paid')),
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payment_project
    ON payment_nodes(project_number, planned_date);

CREATE TABLE IF NOT EXISTS interventions (
    intervention_id TEXT PRIMARY KEY,
    project_number TEXT NOT NULL,
    title TEXT NOT NULL,
    intervention_type TEXT NOT NULL DEFAULT 'management',
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'in_progress', 'closed')),
    owner TEXT,
    due_date TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intervention_project
    ON interventions(project_number, status);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)

    def _migrate(self, connection: sqlite3.Connection) -> None:
        """Apply additive migrations for databases created by earlier MVP builds."""
        milestone_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(milestones)").fetchall()
        }
        additions = {
            "sequence_no": "INTEGER",
            "planned_start": "TEXT",
            "planned_end": "TEXT",
            "deliverable": "TEXT",
            "is_payment_node": "INTEGER NOT NULL DEFAULT 0",
            "payment_node_id": "TEXT",
            "confirmed_by": "TEXT",
            "confirmed_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in milestone_columns:
                connection.execute(
                    f"ALTER TABLE milestones ADD COLUMN {name} {definition}"
                )
        connection.execute(
            "UPDATE milestones SET planned_end = planned_date WHERE planned_end IS NULL"
        )
        connection.commit()

    def active_snapshot_id(self) -> int | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM service_state WHERE key = 'active_snapshot_id'"
            ).fetchone()
            return int(row["value"]) if row else None

    def save_snapshot(self, snapshot: dict[str, Any]) -> int:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT INTO snapshots (
                    schema_version, source_updated_at, imported_at,
                    source_contract_count, source_opportunity_count,
                    output_contract_count, output_opportunity_count,
                    merged_opportunity_count, validation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot["schema_version"],
                    snapshot["updated_at"],
                    snapshot["imported_at"],
                    snapshot["source_stats"]["contract_order"],
                    snapshot["source_stats"]["sales_opportunity"],
                    snapshot["output_stats"]["contract_order"],
                    snapshot["output_stats"]["sales_opportunity"],
                    snapshot["validation"]["merged_opportunities"],
                    json.dumps(snapshot["validation"], ensure_ascii=False),
                ),
            )
            snapshot_id = int(cursor.lastrowid)
            rows = []
            for item in snapshot["records"]:
                rows.append(
                    (
                        snapshot_id,
                        item["record_id"],
                        item["data_type"],
                        item.get("project_number"),
                        item["subject"],
                        item.get("customer"),
                        item.get("category"),
                        item.get("signed_at"),
                        _money_value(item.get("total_amount")),
                        _money_display(item.get("total_amount")),
                        _money_value(item.get("received_amount")),
                        _money_display(item.get("received_amount")),
                        _money_value(item.get("invoiced_amount")),
                        _money_display(item.get("invoiced_amount")),
                        item["owner"],
                        item.get("probability"),
                        item.get("status"),
                        1 if item.get("merged_from_opportunity") else 0,
                        item["updated_at"],
                    )
                )
            connection.executemany(
                """
                INSERT INTO records (
                    snapshot_id, record_id, data_type, project_number, subject,
                    customer, category, signed_at,
                    total_amount, total_amount_display,
                    received_amount, received_amount_display,
                    invoiced_amount, invoiced_amount_display,
                    owner, probability, status, merged_from_opportunity, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.execute(
                """
                INSERT INTO service_state(key, value) VALUES('active_snapshot_id', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(snapshot_id),),
            )
            connection.commit()
            return snapshot_id

    def save_crm_import_log(
        self,
        *,
        snapshot_id: int,
        data_type: str,
        filename: str,
        parsed_rows: int,
        inserted_records: int,
        updated_records: int,
        unchanged_records: int,
        imported_at: str,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO crm_import_logs (
                    snapshot_id, data_type, filename, parsed_rows,
                    inserted_records, updated_records, unchanged_records, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    data_type,
                    filename,
                    parsed_rows,
                    inserted_records,
                    updated_records,
                    unchanged_records,
                    imported_at,
                ),
            )
            log_id = int(cursor.lastrowid)
            connection.commit()
        return {
            "log_id": log_id,
            "snapshot_id": snapshot_id,
            "data_type": data_type,
            "filename": filename,
            "parsed_rows": parsed_rows,
            "inserted_records": inserted_records,
            "updated_records": updated_records,
            "unchanged_records": unchanged_records,
            "imported_at": imported_at,
        }


def _money_value(value: dict[str, str] | None) -> str | None:
    return value["amount"] if value else None


def _money_display(value: dict[str, str] | None) -> str | None:
    return value["display"] if value else None
