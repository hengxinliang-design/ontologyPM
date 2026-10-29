from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .normalizer import customer_name


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
    sales_stage TEXT,
    status TEXT,
    source_updated_at TEXT,
    merged_from_opportunity INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(snapshot_id, record_id)
);

CREATE INDEX IF NOT EXISTS idx_records_snapshot ON records(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_records_project ON records(snapshot_id, project_number);
CREATE INDEX IF NOT EXISTS idx_records_type ON records(snapshot_id, data_type);
CREATE INDEX IF NOT EXISTS idx_records_owner ON records(snapshot_id, owner);
CREATE INDEX IF NOT EXISTS idx_records_status ON records(snapshot_id, status);
CREATE INDEX IF NOT EXISTS idx_records_snapshot_signed_id
    ON records(snapshot_id, signed_at DESC, id ASC);

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
    contract_person_day_rate TEXT,
    tax_rate_percent TEXT,
    external_procurement_amount TEXT,
    commercial_expense_amount TEXT,
    target_gross_margin_percent TEXT,
    target_margin_reason TEXT,
    remaining_person_days_estimate TEXT,
    remaining_estimate_updated_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS annual_operating_plans (
    plan_id TEXT PRIMARY KEY,
    year INTEGER NOT NULL,
    department TEXT NOT NULL,
    new_contract_target TEXT,
    confirmed_revenue_target TEXT,
    collection_target TEXT,
    gross_margin_target_percent TEXT,
    capacity_person_days_target TEXT,
    headcount_target TEXT,
    utilization_target_percent TEXT,
    notes TEXT,
    updated_by TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(year, department)
);

CREATE TABLE IF NOT EXISTS time_entries (
    external_id TEXT PRIMARY KEY,
    project_number TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    employee_name TEXT NOT NULL,
    work_date TEXT NOT NULL,
    hours TEXT NOT NULL,
    overtime_hours TEXT NOT NULL DEFAULT '0.00',
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

-- Date-leading indexes serve monthly/range monitoring without scanning every
-- employee or project first.  The existing indexes remain useful for a known
-- employee/project across a long time range.
CREATE INDEX IF NOT EXISTS idx_time_date_employee_project
    ON time_entries(work_date, employee_name, project_number);

CREATE INDEX IF NOT EXISTS idx_time_date_project
    ON time_entries(work_date, project_number);

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
    overload_utilization_percent TEXT NOT NULL DEFAULT '100.00',
    budget_attention_percent TEXT NOT NULL DEFAULT '80.00',
    budget_high_risk_percent TEXT NOT NULL DEFAULT '90.00',
    default_tax_rate_percent TEXT NOT NULL DEFAULT '6.00',
    default_target_margin_percent TEXT NOT NULL DEFAULT '30.00',
    cost_ahead_warning_points TEXT NOT NULL DEFAULT '20.00',
    overtime_share_attention_percent TEXT NOT NULL DEFAULT '20.00',
    overload_consecutive_months TEXT NOT NULL DEFAULT '2',
    max_concurrent_projects TEXT NOT NULL DEFAULT '4',
    backlog_coverage_percent TEXT NOT NULL DEFAULT '50.00',
    plan_lag_tolerance_points TEXT NOT NULL DEFAULT '5.00',
    control_cost_exceeded_projects TEXT NOT NULL DEFAULT '2',
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

-- Contract payment terms are deliberately separate from payment events.  At
-- project initialization a contract normally defines a ratio, clause and
-- amount, but has no calendar date yet.  A date-bound payment event is only
-- created after the term is associated with a project milestone.
CREATE TABLE IF NOT EXISTS payment_terms (
    payment_term_id TEXT PRIMARY KEY,
    project_number TEXT NOT NULL,
    name TEXT NOT NULL,
    payment_ratio TEXT,
    planned_amount TEXT NOT NULL,
    notes TEXT,
    linked_milestone_id TEXT UNIQUE,
    payment_node_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payment_term_project
    ON payment_terms(project_number, linked_milestone_id);

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

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    permissions_json TEXT NOT NULL DEFAULT '[]',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_username
    ON auth_sessions(username);

CREATE TABLE IF NOT EXISTS ontology_action_log (
    log_id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_pk TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    edits_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'rejected', 'failed')),
    message TEXT,
    actor TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'ontology',
    executed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ontology_action_log_object
    ON ontology_action_log(object_type, object_pk, executed_at DESC);

CREATE TABLE IF NOT EXISTS opportunity_outcomes (
    project_code TEXT PRIMARY KEY,
    outcome TEXT NOT NULL CHECK (outcome IN ('lost', 'abandoned')),
    reason TEXT NOT NULL,
    outcome_date TEXT NOT NULL,
    notes TEXT,
    recorded_by TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS investment_settings (
    config_id TEXT PRIMARY KEY DEFAULT 'default',
    standard_person_day_cost TEXT,
    stall_days INTEGER NOT NULL DEFAULT 90,
    lost_statuses_json TEXT NOT NULL DEFAULT '["失败", "失效", "丢单", "输单", "关闭", "放弃"]',
    internal_codes_json TEXT NOT NULL DEFAULT '["TSG999"]',
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO worktime_baseline_config(config_id, notes, updated_at)
VALUES ('default', '默认工时基线参数，可在页面人工维护。', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));

INSERT OR IGNORE INTO investment_settings(config_id, updated_at)
VALUES ('default', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));

CREATE TABLE IF NOT EXISTS ontology_dataset_versions (
    dataset TEXT PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 0,
    changed_at TEXT
);

CREATE TABLE IF NOT EXISTS ontology_versions (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    api_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT,
    documents_json TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    last_activated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_request_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    returned_rows INTEGER,
    is_import INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_request_metrics_endpoint_created
    ON api_request_metrics(endpoint, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_api_request_metrics_import_created
    ON api_request_metrics(is_import, created_at DESC);
"""


# Physical datasets that back ontology objects.  Every write to one of these
# tables bumps its version through a trigger, so the ontology object index can
# detect staleness no matter which code path (import, action, legacy API)
# changed the data.
# Operating-policy parameters added after the first release; migrations add any that are missing.
POLICY_COLUMN_DEFAULTS = {
    "budget_attention_percent": "80.00",
    "budget_high_risk_percent": "90.00",
    "default_tax_rate_percent": "6.00",
    "default_target_margin_percent": "30.00",
    "cost_ahead_warning_points": "20.00",
    "overtime_share_attention_percent": "20.00",
    "overload_consecutive_months": "2",
    "max_concurrent_projects": "4",
    "backlog_coverage_percent": "50.00",
    "plan_lag_tolerance_points": "5.00",
    "control_cost_exceeded_projects": "2",
}

PROJECT_ECONOMICS_COLUMNS = (
    "contract_person_day_rate", "tax_rate_percent", "external_procurement_amount",
    "commercial_expense_amount", "target_gross_margin_percent", "target_margin_reason",
    "remaining_person_days_estimate", "remaining_estimate_updated_at",
)

DEFAULT_LOST_STATUSES_JSON = '["失败", "失效", "丢单", "输单", "关闭", "放弃"]'

OBJECT_DATASETS = (
    "records",
    "project_settings",
    "time_entries",
    "milestones",
    "payment_nodes",
    "interventions",
    "opportunity_outcomes",
    "investment_settings",
    "payment_terms",
    "worktime_baseline_config",
    "annual_operating_plans",
)


def _dataset_trigger_sql() -> str:
    statements = []
    for dataset in OBJECT_DATASETS:
        statements.append(
            f"INSERT OR IGNORE INTO ontology_dataset_versions(dataset, version) VALUES ('{dataset}', 0);"
        )
        for operation in ("INSERT", "UPDATE", "DELETE"):
            statements.append(
                f"""
CREATE TRIGGER IF NOT EXISTS trg_dataset_version_{dataset}_{operation.lower()}
AFTER {operation} ON {dataset}
BEGIN
    UPDATE ontology_dataset_versions
    SET version = version + 1, changed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE dataset = '{dataset}';
END;"""
            )
    # Switching the active CRM snapshot changes which records are visible.
    for operation in ("INSERT", "UPDATE"):
        statements.append(
            f"""
CREATE TRIGGER IF NOT EXISTS trg_dataset_version_active_snapshot_{operation.lower()}
AFTER {operation} ON service_state
WHEN NEW.key = 'active_snapshot_id'
BEGIN
    UPDATE ontology_dataset_versions
    SET version = version + 1, changed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE dataset = 'records';
END;"""
        )
    return "\n".join(statements)


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        # Make concurrent browser reads wait briefly for an import transaction
        # instead of immediately returning "database is locked".
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)
            connection.executescript(_dataset_trigger_sql())

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
        record_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(records)").fetchall()
        }
        record_additions = {
            "sales_stage": "TEXT",
            "source_updated_at": "TEXT",
        }
        for name, definition in record_additions.items():
            if name not in record_columns:
                connection.execute(f"ALTER TABLE records ADD COLUMN {name} {definition}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_snapshot_stage "
            "ON records(snapshot_id, sales_stage)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_snapshot_source_updated "
            "ON records(snapshot_id, source_updated_at DESC, id ASC)"
        )
        connection.execute(
            "UPDATE milestones SET planned_end = planned_date WHERE planned_end IS NULL"
        )
        time_entry_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(time_entries)").fetchall()
        }
        if "overtime_hours" not in time_entry_columns:
            connection.execute(
                "ALTER TABLE time_entries ADD COLUMN overtime_hours TEXT NOT NULL DEFAULT '0.00'"
            )
        baseline_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(worktime_baseline_config)").fetchall()
        }
        if "overload_utilization_percent" not in baseline_columns:
            connection.execute(
                "ALTER TABLE worktime_baseline_config ADD COLUMN "
                "overload_utilization_percent TEXT NOT NULL DEFAULT '100.00'"
            )
        for name, default in POLICY_COLUMN_DEFAULTS.items():
            if name not in baseline_columns:
                connection.execute(
                    f"ALTER TABLE worktime_baseline_config ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'"
                )
        settings_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(project_settings)").fetchall()
        }
        for name in PROJECT_ECONOMICS_COLUMNS:
            if name not in settings_columns:
                connection.execute(f"ALTER TABLE project_settings ADD COLUMN {name} TEXT")
        # Customer names imported before the CRM bracket pair was dropped (〖TPL〗 → TPL).
        for row in connection.execute(
            "SELECT id, customer FROM records WHERE customer LIKE '〖%' OR customer LIKE '【%' "
            "OR customer LIKE '[%' OR customer LIKE '〔%'"
        ).fetchall():
            cleaned = customer_name(row["customer"])
            if cleaned != row["customer"]:
                connection.execute("UPDATE records SET customer = ? WHERE id = ?", (cleaned, row["id"]))
        # 「失效」 opportunities are closed like 「失败」 ones.  Only rows still on the
        # previous built-in default are upgraded, so a customised list is kept.
        connection.execute(
            "UPDATE investment_settings SET lost_statuses_json = ? WHERE lost_statuses_json = ?",
            (DEFAULT_LOST_STATUSES_JSON, '["失败", "丢单", "输单", "关闭", "放弃"]'),
        )
        # Accelerates the non-destructive semantic duplicate check performed
        # for Excel imports.  Existing historical data is deliberately kept;
        # cleanup requires an explicit, backed-up user operation.
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_xlsx_time_entries_semantic_lookup
            ON time_entries(
                employee_id, project_number, work_date, hours,
                overtime_hours, work_content
            )
            """
        )
        payment_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(payment_nodes)").fetchall()
        }
        if "payment_term_id" not in payment_columns:
            connection.execute("ALTER TABLE payment_nodes ADD COLUMN payment_term_id TEXT")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_nodes_term "
            "ON payment_nodes(payment_term_id) WHERE payment_term_id IS NOT NULL"
        )
        connection.commit()

    def dataset_versions(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT dataset, version FROM ontology_dataset_versions"
            ).fetchall()
        return {row["dataset"]: int(row["version"]) for row in rows}
    def record_api_metric(
        self,
        *,
        endpoint: str,
        method: str,
        status_code: int,
        duration_ms: float,
        returned_rows: int | None,
        is_import: bool,
        created_at: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO api_request_metrics(
                    endpoint, method, status_code, duration_ms, returned_rows,
                    is_import, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    endpoint,
                    method,
                    status_code,
                    duration_ms,
                    returned_rows,
                    int(is_import),
                    created_at,
                ),
            )
            connection.commit()

    def performance_summary(self, *, limit: int = 2000) -> dict[str, Any]:
        """Return recent endpoint timings without relying on SQLite extensions."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT endpoint, method, status_code, duration_ms, returned_rows,
                       is_import, created_at
                FROM api_request_metrics
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        grouped: dict[tuple[str, str, int], list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault((row["endpoint"], row["method"], row["is_import"]), []).append(row)
        endpoints = []
        for (endpoint, method, is_import), samples in sorted(grouped.items()):
            durations = sorted(float(row["duration_ms"]) for row in samples)
            row_counts = [int(row["returned_rows"]) for row in samples if row["returned_rows"] is not None]
            endpoints.append(
                {
                    "endpoint": endpoint,
                    "method": method,
                    "sample_count": len(samples),
                    "p50_ms": _percentile(durations, 0.50),
                    "p95_ms": _percentile(durations, 0.95),
                    "max_ms": round(durations[-1], 2),
                    "average_returned_rows": round(sum(row_counts) / len(row_counts), 2) if row_counts else None,
                    "error_count": sum(row["status_code"] >= 400 for row in samples),
                    "is_import": bool(is_import),
                    "latest_at": max(row["created_at"] for row in samples),
                }
            )
        return {"sample_limit": limit, "sample_count": len(rows), "endpoints": endpoints}


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
                        item.get("sales_stage"),
                        item.get("status"),
                        item.get("source_updated_at"),
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
                    owner, probability, sales_stage, status, source_updated_at,
                    merged_from_opportunity, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, math.ceil(len(values) * percentile) - 1))
    return round(values[index], 2)
