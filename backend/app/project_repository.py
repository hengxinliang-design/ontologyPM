from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .db import Database
from .repository import SnapshotRepository


CAPPED_TYPES = {"construction", "maintenance"}


class ProjectRepository:
    def __init__(self, database: Database, timezone: str):
        self.database = database
        self.timezone = timezone
        self.records = SnapshotRepository(database)

    def list_projects(
        self,
        *,
        contract_type: str | None = None,
        lifecycle_status: str | None = None,
        risk_level: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        projects = self._build_all_projects()
        if contract_type:
            projects = [p for p in projects if p["contract_type"] == contract_type]
        if lifecycle_status:
            projects = [p for p in projects if p["lifecycle_status"] == lifecycle_status]
        if risk_level:
            projects = [p for p in projects if p["risk_level"] == risk_level]
        if query:
            needle = query.casefold()
            projects = [
                p
                for p in projects
                if needle
                in " ".join(
                    [
                        p["project_number"],
                        p["subject"],
                        p.get("customer") or "",
                        p.get("project_manager") or "",
                    ]
                ).casefold()
            ]
        projects.sort(key=lambda p: (_risk_rank(p["risk_level"]), p["project_number"]))
        return {
            "data": projects,
            "summary": {
                "total_projects": len(projects),
                "contract_value": _decimal_sum(p["contract_value"] for p in projects),
                "received_amount": _decimal_sum(p["received_amount"] for p in projects),
                "critical_projects": sum(p["risk_level"] == "critical" for p in projects),
                "warning_projects": sum(p["risk_level"] == "warning" for p in projects),
            },
        }

    def get_project(self, project_number: str) -> dict[str, Any] | None:
        for project in self._build_all_projects(include_details=True):
            if project["project_number"] == project_number:
                return project
        return None

    def upsert_settings(self, project_number: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        current = self._settings().get(project_number, {})
        values = {
            "contract_type": payload.get("contract_type", current.get("contract_type", "unknown")),
            "lifecycle_status": payload.get(
                "lifecycle_status", current.get("lifecycle_status", "planned")
            ),
            "planned_person_days": _optional_decimal_text(
                payload.get("planned_person_days", current.get("planned_person_days"))
            ),
            "hours_per_person_day": _optional_decimal_text(
                payload.get("hours_per_person_day", current.get("hours_per_person_day", "8.00"))
            )
            or "8.00",
            "planned_start": payload.get("planned_start", current.get("planned_start")),
            "planned_end": payload.get("planned_end", current.get("planned_end")),
            "project_manager": payload.get(
                "project_manager", current.get("project_manager")
            ),
            "notes": payload.get("notes", current.get("notes")),
        }
        if values["contract_type"] in CAPPED_TYPES and not values["planned_person_days"]:
            raise ValueError("planned_person_days is required for capped contract types")
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO project_settings(
                    project_number, contract_type, lifecycle_status,
                    planned_person_days, hours_per_person_day, planned_start,
                    planned_end, project_manager, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_number) DO UPDATE SET
                    contract_type=excluded.contract_type,
                    lifecycle_status=excluded.lifecycle_status,
                    planned_person_days=excluded.planned_person_days,
                    hours_per_person_day=excluded.hours_per_person_day,
                    planned_start=excluded.planned_start,
                    planned_end=excluded.planned_end,
                    project_manager=excluded.project_manager,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    project_number,
                    values["contract_type"],
                    values["lifecycle_status"],
                    values["planned_person_days"],
                    values["hours_per_person_day"],
                    values["planned_start"],
                    values["planned_end"],
                    values["project_manager"],
                    values["notes"],
                    now,
                ),
            )
            connection.commit()
        return self.get_project(project_number) or {"project_number": project_number}

    def import_time_entries(self, entries: list[dict[str, Any]]) -> dict[str, int]:
        now = self._now()
        imported = 0
        with self.database.connect() as connection:
            for entry in entries:
                hours = _decimal_text(entry["hours"])
                approved_hours = _decimal_text(entry.get("approved_hours", "0"))
                if Decimal(hours) > 24 or Decimal(approved_hours) > 24:
                    raise ValueError("hours must be between 0 and 24")
                connection.execute(
                    """
                    INSERT INTO time_entries(
                        external_id, project_number, employee_id, employee_name,
                        work_date, hours, approved_hours, approval_status,
                        work_content, source_updated_at, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(external_id) DO UPDATE SET
                        project_number=excluded.project_number,
                        employee_id=excluded.employee_id,
                        employee_name=excluded.employee_name,
                        work_date=excluded.work_date,
                        hours=excluded.hours,
                        approved_hours=excluded.approved_hours,
                        approval_status=excluded.approval_status,
                        work_content=excluded.work_content,
                        source_updated_at=excluded.source_updated_at,
                        imported_at=excluded.imported_at
                    """,
                    (
                        entry["external_id"],
                        entry["project_number"],
                        entry["employee_id"],
                        entry["employee_name"],
                        entry["work_date"],
                        hours,
                        approved_hours,
                        entry["approval_status"],
                        entry["work_content"],
                        entry["source_updated_at"],
                        now,
                    ),
                )
                imported += 1
            connection.commit()
        return {"imported": imported}

    def create_milestone(self, project_number: str, payload: dict[str, Any]) -> dict[str, Any]:
        milestone_id = "ms_" + uuid.uuid4().hex[:16]
        planned_end = payload.get("planned_end") or payload.get("planned_date")
        if not planned_end:
            raise ValueError("planned_end is required")
        planned_start = payload.get("planned_start") or planned_end
        is_payment_node = bool(payload.get("is_payment_node", False))
        payment_date = payload.get("payment_date")
        payment_amount = payload.get("payment_amount")
        if is_payment_node and (not payment_date or payment_amount in (None, "")):
            raise ValueError("payment_date and payment_amount are required for payment milestones")
        payment_node_id = "pay_" + uuid.uuid4().hex[:16] if is_payment_node else None
        with self.database.connect() as connection:
            sequence_no = payload.get("sequence_no")
            if sequence_no is None:
                row = connection.execute(
                    "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_no FROM milestones WHERE project_number = ?",
                    (project_number,),
                ).fetchone()
                sequence_no = row["next_no"]
            connection.execute(
                """
                INSERT INTO milestones(
                    milestone_id, project_number, sequence_no, name, planned_date,
                    planned_start, planned_end, completed_date, status, owner,
                    deliverable, is_payment_node, payment_node_id, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    milestone_id,
                    project_number,
                    sequence_no,
                    payload["name"],
                    planned_end,
                    planned_start,
                    planned_end,
                    payload.get("completed_date"),
                    payload.get("status", "planned"),
                    payload.get("owner"),
                    payload.get("deliverable"),
                    1 if is_payment_node else 0,
                    payment_node_id,
                    payload.get("notes"),
                    self._now(),
                ),
            )
            if is_payment_node:
                connection.execute(
                    """
                    INSERT INTO payment_nodes(
                        payment_node_id, project_number, name, planned_date,
                        planned_amount, actual_date, actual_amount, status, notes, updated_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, '0.00', 'planned', ?, ?)
                    """,
                    (
                        payment_node_id,
                        project_number,
                        payload["name"],
                        payment_date,
                        _decimal_text(payment_amount),
                        payload.get("notes"),
                        self._now(),
                    ),
                )
            connection.commit()
        return self._milestone_with_payment(milestone_id)

    def update_milestone(
        self, project_number: str, milestone_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        current = self._row_by_project_id(
            "milestones", "milestone_id", milestone_id, project_number
        )
        if current is None:
            raise KeyError("milestone not found")
        if current["status"] == "completed":
            raise ValueError("completed milestones are locked and cannot be edited")
        if payload.get("status") == "completed":
            raise ValueError("use the confirm endpoint to complete a milestone")
        values = {**current, **payload}
        planned_end = values.get("planned_end") or values.get("planned_date")
        is_payment_node = bool(values.get("is_payment_node"))
        payment_node_id = current.get("payment_node_id")
        removed_payment_node_id = None
        payment_date = payload.get("payment_date")
        payment_amount = payload.get("payment_amount")
        if is_payment_node and not payment_node_id:
            if not payment_date or payment_amount in (None, ""):
                raise ValueError("payment_date and payment_amount are required for payment milestones")
            payment_node_id = "pay_" + uuid.uuid4().hex[:16]
        elif not is_payment_node and payment_node_id:
            removed_payment_node_id = payment_node_id
            payment_node_id = None
        with self.database.connect() as connection:
            if removed_payment_node_id:
                payment = connection.execute(
                    "SELECT status FROM payment_nodes WHERE payment_node_id = ?",
                    (removed_payment_node_id,),
                ).fetchone()
                if payment and payment["status"] == "paid":
                    raise ValueError("a confirmed payment node cannot be removed")
            connection.execute(
                """
                UPDATE milestones SET sequence_no=?, name=?, planned_date=?,
                    planned_start=?, planned_end=?, status=?, owner=?, deliverable=?,
                    is_payment_node=?, payment_node_id=?, notes=?, updated_at=?
                WHERE milestone_id=? AND project_number=?
                """,
                (
                    values.get("sequence_no"), values["name"], planned_end,
                    values.get("planned_start") or planned_end, planned_end,
                    values.get("status", "planned"), values.get("owner"),
                    values.get("deliverable"), 1 if is_payment_node else 0,
                    payment_node_id, values.get("notes"), self._now(),
                    milestone_id, project_number,
                ),
            )
            if is_payment_node:
                existing_payment = connection.execute(
                    "SELECT * FROM payment_nodes WHERE payment_node_id = ?",
                    (payment_node_id,),
                ).fetchone()
                if existing_payment:
                    connection.execute(
                        """
                        UPDATE payment_nodes SET name=?, planned_date=?, planned_amount=?,
                            notes=?, updated_at=? WHERE payment_node_id=?
                        """,
                        (
                            values["name"], payment_date or existing_payment["planned_date"],
                            _decimal_text(payment_amount if payment_amount not in (None, "") else existing_payment["planned_amount"]),
                            values.get("notes"), self._now(), payment_node_id,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO payment_nodes(payment_node_id, project_number, name,
                            planned_date, planned_amount, actual_amount, status, notes, updated_at)
                        VALUES (?, ?, ?, ?, ?, '0.00', 'planned', ?, ?)
                        """,
                        (payment_node_id, project_number, values["name"], payment_date,
                         _decimal_text(payment_amount), values.get("notes"), self._now()),
                    )
            elif removed_payment_node_id:
                connection.execute(
                    "DELETE FROM payment_nodes WHERE payment_node_id = ?",
                    (removed_payment_node_id,),
                )
            connection.commit()
        return self._milestone_with_payment(milestone_id)

    def confirm_milestone(
        self, project_number: str, milestone_id: str, confirmed_by: str
    ) -> dict[str, Any]:
        now = self._now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE milestones SET status='completed', completed_date=?,
                    confirmed_by=?, confirmed_at=?, updated_at=?
                WHERE milestone_id=? AND project_number=?
                """,
                (date.today().isoformat(), confirmed_by, now, now, milestone_id, project_number),
            )
            if cursor.rowcount != 1:
                raise KeyError("milestone not found")
            connection.commit()
        return self._milestone_with_payment(milestone_id)

    def confirm_payment_node(
        self, project_number: str, payment_node_id: str, confirmed_by: str,
        actual_amount: Any | None = None, actual_date: str | None = None,
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM payment_nodes WHERE payment_node_id=? AND project_number=?",
                (payment_node_id, project_number),
            ).fetchone()
            if row is None:
                raise KeyError("payment node not found")
            amount = _decimal_text(actual_amount if actual_amount not in (None, "") else row["planned_amount"])
            confirmed_note = f"确认人：{confirmed_by}"
            notes = f"{row['notes']}\n{confirmed_note}" if row["notes"] else confirmed_note
            connection.execute(
                """
                UPDATE payment_nodes SET actual_date=?, actual_amount=?, status='paid',
                    notes=?, updated_at=? WHERE payment_node_id=?
                """,
                (actual_date or date.today().isoformat(), amount, notes, self._now(), payment_node_id),
            )
            connection.commit()
        return self._row_by_id("payment_nodes", "payment_node_id", payment_node_id)

    def create_payment_node(
        self, project_number: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        payment_id = "pay_" + uuid.uuid4().hex[:16]
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO payment_nodes(
                    payment_node_id, project_number, name, planned_date,
                    planned_amount, actual_date, actual_amount, status, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payment_id,
                    project_number,
                    payload["name"],
                    payload["planned_date"],
                    _decimal_text(payload["planned_amount"]),
                    payload.get("actual_date"),
                    _decimal_text(payload.get("actual_amount", "0")),
                    payload.get("status", "planned"),
                    payload.get("notes"),
                    self._now(),
                ),
            )
            connection.commit()
        return self._row_by_id("payment_nodes", "payment_node_id", payment_id)

    def create_intervention(
        self, project_number: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        intervention_id = "act_" + uuid.uuid4().hex[:16]
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO interventions(
                    intervention_id, project_number, title, intervention_type,
                    status, owner, due_date, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intervention_id,
                    project_number,
                    payload["title"],
                    payload.get("intervention_type", "management"),
                    payload.get("status", "open"),
                    payload.get("owner"),
                    payload.get("due_date"),
                    payload.get("notes"),
                    self._now(),
                ),
            )
            connection.commit()
        return self._row_by_id("interventions", "intervention_id", intervention_id)

    def ensure_demo_data(self) -> None:
        with self.database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) AS n FROM project_settings").fetchone()["n"]
        if count:
            return
        self.upsert_settings(
            "TPL149",
            {
                "contract_type": "construction",
                "lifecycle_status": "active",
                "planned_person_days": "30",
                "hours_per_person_day": "8",
                "planned_start": "2026-06-16",
                "planned_end": "2026-08-31",
                "project_manager": "Li Qiang",
                "notes": "ERP/MOM 与 SRM 接口项目",
            },
        )
        self.upsert_settings(
            "ZMJ113",
            {
                "contract_type": "open_ended",
                "lifecycle_status": "active",
                "project_manager": "Li Qiang",
            },
        )
        self.upsert_settings(
            "WDR101",
            {
                "contract_type": "sales",
                "lifecycle_status": "planned",
                "project_manager": "Li Qiang",
            },
        )
        self.import_time_entries(
            [
                {
                    "external_id": "DEMO-TIME-001",
                    "project_number": "TPL149",
                    "employee_id": "E001",
                    "employee_name": "Zhang San",
                    "work_date": "2026-06-29",
                    "hours": "8",
                    "approved_hours": "8",
                    "approval_status": "approved",
                    "work_content": "完成接口方案设计与评审",
                    "source_updated_at": "2026-06-29T18:00:00+08:00",
                },
                {
                    "external_id": "DEMO-TIME-002",
                    "project_number": "TPL149",
                    "employee_id": "E002",
                    "employee_name": "Li Si",
                    "work_date": "2026-06-30",
                    "hours": "8",
                    "approved_hours": "8",
                    "approval_status": "approved",
                    "work_content": "SRM 接口开发与单元测试",
                    "source_updated_at": "2026-06-30T18:00:00+08:00",
                },
                {
                    "external_id": "DEMO-TIME-003",
                    "project_number": "TPL149",
                    "employee_id": "E001",
                    "employee_name": "Zhang San",
                    "work_date": "2026-07-01",
                    "hours": "4",
                    "approved_hours": "4",
                    "approval_status": "approved",
                    "work_content": "联调问题处理",
                    "source_updated_at": "2026-07-01T18:00:00+08:00",
                },
            ]
        )
        self.create_milestone(
            "TPL149",
            {
                "name": "接口方案确认",
                "planned_date": "2026-07-10",
                "status": "in_progress",
                "owner": "Li Qiang",
            },
        )
        self.create_milestone(
            "TPL149",
            {
                "name": "项目上线",
                "planned_date": "2026-08-15",
                "status": "planned",
                "owner": "Li Qiang",
            },
        )
        self.create_payment_node(
            "TPL149",
            {
                "name": "首期款",
                "planned_date": "2026-07-20",
                "planned_amount": "74950",
                "status": "invoiced",
            },
        )
        self.create_payment_node(
            "TPL149",
            {
                "name": "验收尾款",
                "planned_date": "2026-09-15",
                "planned_amount": "74950",
            },
        )

    def _build_all_projects(self, include_details: bool = False) -> list[dict[str, Any]]:
        snapshot = self.records.export_records()
        crm_records = snapshot["records"] if snapshot else []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in crm_records:
            project = record.get("project_number")
            if project:
                grouped.setdefault(project, []).append(record)

        settings = self._settings()
        time_entries = self._rows_grouped("time_entries", "project_number", "work_date DESC")
        milestones = self._rows_grouped("milestones", "project_number", "planned_date")
        payments = self._rows_grouped("payment_nodes", "project_number", "planned_date")
        interventions = self._rows_grouped("interventions", "project_number", "updated_at DESC")
        project_numbers = (
            set(grouped) | set(settings) | set(time_entries) | set(milestones)
            | set(payments) | set(interventions)
        )
        return [
            self._build_project(
                project_number,
                grouped.get(project_number, []),
                settings.get(project_number, {}),
                time_entries.get(project_number, []),
                milestones.get(project_number, []),
                payments.get(project_number, []),
                interventions.get(project_number, []),
                include_details,
            )
            for project_number in project_numbers
        ]

    def _build_project(
        self,
        project_number: str,
        crm_records: list[dict[str, Any]],
        settings: dict[str, Any],
        time_entries: list[dict[str, Any]],
        milestones: list[dict[str, Any]],
        payments: list[dict[str, Any]],
        interventions: list[dict[str, Any]],
        include_details: bool,
    ) -> dict[str, Any]:
        contracts = [r for r in crm_records if r["data_type"] == "contract_order"]
        opportunities = [r for r in crm_records if r["data_type"] == "sales_opportunity"]
        primary = (contracts or opportunities or [{}])[0]
        hours_per_day = Decimal(settings.get("hours_per_person_day") or "8")
        reported_hours = sum(
            (Decimal(row["hours"]) for row in time_entries),
            Decimal("0"),
        )
        customer_confirmed_hours = sum(
            (Decimal(row["approved_hours"]) for row in time_entries if row["approval_status"] == "approved"),
            Decimal("0"),
        )
        reported_days = reported_hours / hours_per_day if hours_per_day else Decimal("0")
        customer_confirmed_days = (
            customer_confirmed_hours / hours_per_day if hours_per_day else Decimal("0")
        )
        confirmation_gap_hours = max(
            reported_hours - customer_confirmed_hours, Decimal("0")
        )
        confirmation_gap_days = (
            confirmation_gap_hours / hours_per_day if hours_per_day else Decimal("0")
        )
        planned_days = (
            Decimal(settings["planned_person_days"])
            if settings.get("planned_person_days")
            else None
        )
        utilization = (
            reported_days / planned_days * 100 if planned_days and planned_days > 0 else None
        )
        contract_value = _sum_money(contracts, "total_amount")
        received_amount = _sum_money(contracts, "received_amount")
        invoiced_amount = _sum_money(contracts, "invoiced_amount")
        today_date = date.today()
        today = today_date.isoformat()
        overdue_milestones = sum(
            row["planned_date"] < today and row["status"] != "completed"
            for row in milestones
        )
        overdue_payments = sum(
            row["planned_date"] < today and row["status"] != "paid" for row in payments
        )
        contract_type = settings.get("contract_type", "unknown")
        lifecycle = settings.get("lifecycle_status", "planned")
        risk_reasons: list[str] = []
        critical = False
        if utilization is not None and utilization > 100:
            critical = True
            risk_reasons.append("实际人天已超过预算")
        elif utilization is not None and utilization >= 80 and lifecycle not in {"completed", "closed"}:
            risk_reasons.append("人天预算使用率已达到 80%")
        if overdue_milestones:
            critical = True
            risk_reasons.append(f"{overdue_milestones} 个里程碑逾期")
        if overdue_payments:
            critical = True
            risk_reasons.append(f"{overdue_payments} 个回款节点逾期")
        if contract_type == "unknown":
            risk_reasons.append("合同类型尚未设置")
        if contract_type in CAPPED_TYPES and planned_days is None:
            risk_reasons.append("预算人天尚未设置")
        if not milestones and lifecycle in {"planned", "active"}:
            risk_reasons.append("尚未设置里程碑")
        maintenance_reasons: list[str] = []
        if contract_type == "unknown":
            maintenance_reasons.append("选择合同类别")
        if contract_type in CAPPED_TYPES and planned_days is None:
            maintenance_reasons.append("填写初始预算人天")
        if not milestones:
            maintenance_reasons.append("制定里程碑计划")

        payment_by_id = {row["payment_node_id"]: row for row in payments}
        enriched_milestones: list[dict[str, Any]] = []
        todo_events: list[dict[str, Any]] = []
        for row in milestones:
            item = dict(row)
            item["planned_start"] = item.get("planned_start") or item["planned_date"]
            item["planned_end"] = item.get("planned_end") or item["planned_date"]
            linked_payment = payment_by_id.get(item.get("payment_node_id"))
            item["payment_date"] = linked_payment["planned_date"] if linked_payment else None
            item["payment_amount"] = linked_payment["planned_amount"] if linked_payment else None
            item["payment_status"] = linked_payment["status"] if linked_payment else None
            item["deadline"] = _deadline(item["planned_end"], item["status"] == "completed", today_date)
            enriched_milestones.append(item)
            todo_events.append(
                _todo_event(
                    "milestone", item["milestone_id"], item["name"],
                    item["planned_end"], item["status"] == "completed", today_date,
                    deliverable=item.get("deliverable"),
                )
            )

        for row in payments:
            todo_events.append(
                _todo_event(
                    "payment", row["payment_node_id"], row["name"],
                    row["planned_date"], row["status"] == "paid", today_date,
                    amount=row["planned_amount"],
                )
            )
        todo_events.sort(
            key=lambda event: (event["completed"], event["due_date"], event["event_type"])
        )
        if confirmation_gap_hours > 0:
            risk_reasons.append(
                f"已填报工时与客户确认工时相差 {_format_decimal(confirmation_gap_days)} 人天"
            )
        anomaly_alerts: list[dict[str, Any]] = []
        if utilization is not None and utilization > 100:
            anomaly_alerts.append(
                {
                    "code": "BUDGET_OVERRUN",
                    "severity": "critical",
                    "title": "工时已超出初始预算",
                    "detail": f"已填报 {_format_decimal(reported_days)} 人天，预算 {_format_decimal(planned_days)} 人天",
                }
            )
        elif utilization is not None and utilization >= 80:
            anomaly_alerts.append(
                {
                    "code": "BUDGET_NEAR_LIMIT",
                    "severity": "warning",
                    "title": "工时预算接近上限",
                    "detail": f"当前已使用 {round(float(utilization), 1)}%",
                }
            )
        if confirmation_gap_hours > 0:
            anomaly_alerts.append(
                {
                    "code": "TIME_CONFIRMATION_GAP",
                    "severity": "warning",
                    "title": "存在客户未确认工时",
                    "detail": (
                        f"已填报 {_format_decimal(reported_days)} 人天，客户确认 "
                        f"{_format_decimal(customer_confirmed_days)} 人天，缺口 "
                        f"{_format_decimal(confirmation_gap_days)} 人天"
                    ),
                }
            )
        for event in todo_events:
            if event["completed"] or event["urgency"] not in {
                "overdue", "due_today", "due_soon"
            }:
                continue
            anomaly_alerts.append(
                {
                    "code": "MILESTONE_DUE" if event["event_type"] == "milestone" else "PAYMENT_DUE",
                    "severity": "critical" if event["urgency"] == "overdue" else "warning",
                    "title": f"{event['name']} · {event['label']}",
                    "detail": (
                        "里程碑尚未确认完成"
                        if event["event_type"] == "milestone"
                        else f"回款尚未确认到账，计划金额 {_format_decimal(Decimal(event['amount'] or '0'))} 元"
                    ),
                }
            )
        if maintenance_reasons:
            anomaly_alerts.append(
                {
                    "code": "MANUAL_MAINTENANCE_REQUIRED",
                    "severity": "warning",
                    "title": "同步后人工维护尚未完成",
                    "detail": "、".join(maintenance_reasons),
                }
            )
        risk_level = "critical" if critical else "warning" if risk_reasons else "healthy"

        project = {
            "project_number": project_number,
            "subject": primary.get("subject") or project_number,
            "customer": primary.get("customer"),
            "owner": primary.get("owner"),
            "project_manager": settings.get("project_manager") or primary.get("owner"),
            "contract_type": contract_type,
            "lifecycle_status": lifecycle,
            "planned_start": settings.get("planned_start"),
            "planned_end": settings.get("planned_end"),
            "contract_count": len(contracts),
            "opportunity_count": len(opportunities),
            "contract_value": _format_decimal(contract_value),
            "received_amount": _format_decimal(received_amount),
            "invoiced_amount": _format_decimal(invoiced_amount),
            "planned_person_days": _format_decimal(planned_days) if planned_days is not None else None,
            "reported_hours": _format_decimal(reported_hours),
            "reported_person_days": _format_decimal(reported_days),
            "customer_confirmed_hours": _format_decimal(customer_confirmed_hours),
            "customer_confirmed_person_days": _format_decimal(customer_confirmed_days),
            "confirmation_gap_hours": _format_decimal(confirmation_gap_hours),
            "confirmation_gap_person_days": _format_decimal(confirmation_gap_days),
            "actual_hours": _format_decimal(reported_hours),
            "actual_person_days": _format_decimal(reported_days),
            "budget_utilization": round(float(utilization), 1) if utilization is not None else None,
            "billable_person_days": _format_decimal(customer_confirmed_days) if contract_type == "open_ended" else None,
            "milestone_total": len(milestones),
            "milestone_completed": sum(row["status"] == "completed" for row in milestones),
            "overdue_milestones": overdue_milestones,
            "payment_node_total": len(payments),
            "overdue_payment_nodes": overdue_payments,
            "open_interventions": sum(row["status"] != "closed" for row in interventions),
            "maintenance_complete": not maintenance_reasons,
            "maintenance_reasons": maintenance_reasons,
            "pending_todos": sum(not event["completed"] for event in todo_events),
            "overdue_todos": sum(
                event["urgency"] == "overdue" for event in todo_events
            ),
            "next_todo": next(
                (event for event in todo_events if not event["completed"]), None
            ),
            "anomaly_count": len(anomaly_alerts),
            "anomaly_alerts": anomaly_alerts,
            "risk_level": risk_level,
            "risk_reasons": risk_reasons,
        }
        if include_details:
            project.update(
                {
                    "crm_records": crm_records,
                    "time_entries": time_entries[:30],
                    "milestones": enriched_milestones,
                    "payment_nodes": payments,
                    "interventions": interventions,
                    "settings": settings,
                    "todo_events": todo_events,
                }
            )
        return project

    def _settings(self) -> dict[str, dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM project_settings").fetchall()
        return {row["project_number"]: dict(row) for row in rows}

    def _rows_grouped(
        self, table: str, key: str, order_by: str
    ) -> dict[str, list[dict[str, Any]]]:
        with self.database.connect() as connection:
            rows = connection.execute(f"SELECT * FROM {table} ORDER BY {order_by}").fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            item = dict(row)
            grouped.setdefault(item[key], []).append(item)
        return grouped

    def _row_by_id(self, table: str, column: str, value: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE {column} = ?", (value,)
            ).fetchone()
        return dict(row)

    def _row_by_project_id(
        self, table: str, column: str, value: str, project_number: str
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE {column} = ? AND project_number = ?",
                (value, project_number),
            ).fetchone()
        return dict(row) if row else None

    def _milestone_with_payment(self, milestone_id: str) -> dict[str, Any]:
        milestone = self._row_by_id("milestones", "milestone_id", milestone_id)
        milestone["planned_start"] = milestone.get("planned_start") or milestone["planned_date"]
        milestone["planned_end"] = milestone.get("planned_end") or milestone["planned_date"]
        payment_id = milestone.get("payment_node_id")
        payment = (
            self._row_by_id("payment_nodes", "payment_node_id", payment_id)
            if payment_id else None
        )
        milestone["payment_date"] = payment["planned_date"] if payment else None
        milestone["payment_amount"] = payment["planned_amount"] if payment else None
        milestone["payment_status"] = payment["status"] if payment else None
        return milestone

    def _now(self) -> str:
        return datetime.now(ZoneInfo(self.timezone)).isoformat(timespec="seconds")


def _sum_money(records: list[dict[str, Any]], field: str) -> Decimal:
    return sum(
        (Decimal(record[field]["amount"]) for record in records if record.get(field)),
        Decimal("0"),
    )


def _decimal_sum(values: Any) -> str:
    return _format_decimal(sum((Decimal(value) for value in values), Decimal("0")))


def _decimal_text(value: Any) -> str:
    return _format_decimal(Decimal(str(value)))


def _optional_decimal_text(value: Any) -> str | None:
    return _decimal_text(value) if value not in (None, "") else None


def _format_decimal(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def _risk_rank(value: str) -> int:
    return {"critical": 0, "warning": 1, "healthy": 2}.get(value, 3)


def _deadline(due_date: str, completed: bool, today: date) -> dict[str, Any]:
    days = (date.fromisoformat(due_date) - today).days
    if completed:
        urgency = "completed"
        label = "已确认完成"
    elif days < 0:
        urgency = "overdue"
        label = f"已延期 {abs(days)} 天"
    elif days == 0:
        urgency = "due_today"
        label = "今天到期"
    elif days <= 7:
        urgency = "due_soon"
        label = f"还有 {days} 天"
    else:
        urgency = "upcoming"
        label = f"还有 {days} 天"
    return {"days_remaining": days, "urgency": urgency, "label": label}


def _todo_event(
    event_type: str,
    event_id: str,
    name: str,
    due_date: str,
    completed: bool,
    today: date,
    *,
    amount: str | None = None,
    deliverable: str | None = None,
) -> dict[str, Any]:
    deadline = _deadline(due_date, completed, today)
    return {
        "event_type": event_type,
        "event_id": event_id,
        "name": name,
        "due_date": due_date,
        "amount": amount,
        "deliverable": deliverable,
        "completed": completed,
        **deadline,
    }
