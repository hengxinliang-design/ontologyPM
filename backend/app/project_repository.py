from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from . import maintenance_rules as rules
from .maintenance_rules import CAPPED_TYPES
from .china_holiday_calendar import ChinaHolidayCalendarService
from .db import Database
from .pagination import CursorError, decode_cursor, encode_cursor
from .repository import SnapshotRepository


INTERNAL_NONPROJECT_CODE = "TSG999"
# Effort counts submitted and approved time; rejected entries were ruled out of the project, drafts were never submitted.
EFFORT_APPROVAL_STATUSES = frozenset({"submitted", "approved"})
# Excel exports carry no reliable update time, so a re-import may only move a submitted entry to a final status.
FINAL_APPROVAL_STATUSES = frozenset({"approved", "rejected"})
LIFECYCLE_STATUS_ORDER = ("planned", "active", "on_hold", "completed", "closed")


class ProjectRepository:
    def __init__(
        self,
        database: Database,
        timezone: str,
        require_crm_project_match: bool = True,
        holiday_calendar: ChinaHolidayCalendarService | None = None,
    ):
        self.database = database
        self.timezone = timezone
        self.require_crm_project_match = require_crm_project_match
        self.holiday_calendar = holiday_calendar
        self.records = SnapshotRepository(database)


    def get_worktime_baseline(self) -> dict[str, Any]:
        self._ensure_worktime_baseline()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM worktime_baseline_config WHERE config_id = 'default'"
            ).fetchone()
        return dict(row)

    def update_worktime_baseline(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_worktime_baseline()
        values = {
            "standard_work_days_per_year": _decimal_text(
                payload.get(
                    "standard_work_days_per_year",
                    current["standard_work_days_per_year"],
                )
            ),
            "standard_hours_per_day": _decimal_text(
                payload.get("standard_hours_per_day", current["standard_hours_per_day"])
            ),
            "annual_baseline_hours": _decimal_text(
                payload.get("annual_baseline_hours", current["annual_baseline_hours"])
            ),
            "effective_baseline_hours": _decimal_text(
                payload.get(
                    "effective_baseline_hours", current["effective_baseline_hours"]
                )
            ),
            "monthly_baseline_hours": _decimal_text(
                payload.get("monthly_baseline_hours", current["monthly_baseline_hours"])
            ),
            "quarterly_baseline_hours": _decimal_text(
                payload.get(
                    "quarterly_baseline_hours", current["quarterly_baseline_hours"]
                )
            ),
            "minimum_utilization_percent": _decimal_text(
                payload.get(
                    "minimum_utilization_percent",
                    current["minimum_utilization_percent"],
                )
            ),
            "excellent_utilization_percent": _decimal_text(
                payload.get(
                    "excellent_utilization_percent",
                    current["excellent_utilization_percent"],
                )
            ),
            "attention_utilization_percent": _decimal_text(
                payload.get(
                    "attention_utilization_percent",
                    current["attention_utilization_percent"],
                )
            ),
            "overload_utilization_percent": _decimal_text(
                payload.get(
                    "overload_utilization_percent",
                    current["overload_utilization_percent"],
                )
            ),
            "budget_attention_percent": _decimal_text(
                payload.get("budget_attention_percent", current["budget_attention_percent"])
            ),
            "budget_high_risk_percent": _decimal_text(
                payload.get("budget_high_risk_percent", current["budget_high_risk_percent"])
            ),
            "notes": payload.get("notes", current["notes"]),
        }
        for name in POLICY_RANGES:
            values[name] = _decimal_text(payload.get(name, current[name]))
        _validate_worktime_baseline(values)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO worktime_baseline_config(
                    config_id, standard_work_days_per_year, standard_hours_per_day,
                    annual_baseline_hours, effective_baseline_hours,
                    monthly_baseline_hours, quarterly_baseline_hours,
                    minimum_utilization_percent, excellent_utilization_percent,
                    attention_utilization_percent, overload_utilization_percent,
                    budget_attention_percent, budget_high_risk_percent,
                    notes, updated_at
                ) VALUES ('default', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(config_id) DO UPDATE SET
                    standard_work_days_per_year=excluded.standard_work_days_per_year,
                    standard_hours_per_day=excluded.standard_hours_per_day,
                    annual_baseline_hours=excluded.annual_baseline_hours,
                    effective_baseline_hours=excluded.effective_baseline_hours,
                    monthly_baseline_hours=excluded.monthly_baseline_hours,
                    quarterly_baseline_hours=excluded.quarterly_baseline_hours,
                    minimum_utilization_percent=excluded.minimum_utilization_percent,
                    excellent_utilization_percent=excluded.excellent_utilization_percent,
                    attention_utilization_percent=excluded.attention_utilization_percent,
                    overload_utilization_percent=excluded.overload_utilization_percent,
                    budget_attention_percent=excluded.budget_attention_percent,
                    budget_high_risk_percent=excluded.budget_high_risk_percent,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    values["standard_work_days_per_year"],
                    values["standard_hours_per_day"],
                    values["annual_baseline_hours"],
                    values["effective_baseline_hours"],
                    values["monthly_baseline_hours"],
                    values["quarterly_baseline_hours"],
                    values["minimum_utilization_percent"],
                    values["excellent_utilization_percent"],
                    values["attention_utilization_percent"],
                    values["overload_utilization_percent"],
                    values["budget_attention_percent"],
                    values["budget_high_risk_percent"],
                    values["notes"],
                    self._now(),
                ),
            )
            connection.execute(
                "UPDATE worktime_baseline_config SET "
                + ", ".join(f"{name}=?" for name in POLICY_RANGES)
                + " WHERE config_id = 'default'",
                tuple(values[name] for name in POLICY_RANGES),
            )
            connection.commit()
        return self.get_worktime_baseline()

    def workday_summary(self, start_date: str, end_date: str) -> dict[str, Any]:
        """China working-day calendar for a date range (statutory holidays and make-up days)."""
        if start_date > end_date:
            raise ValueError("start_date must be on or before end_date")
        return self._workday_summary(start_date, end_date)

    def _workday_summary(self, start_date: str, end_date: str) -> dict[str, Any]:
        """Return the monthly/period capacity calendar used by PMO metrics."""
        if self.holiday_calendar is not None:
            return self.holiday_calendar.workday_summary(start_date, end_date)
        # Repository-only callers (including lightweight data migrations) still
        # receive a deterministic weekday calendar when no subscription service
        # has been wired in.
        current = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        working_days = 0
        while current <= end:
            if current.weekday() < 5:
                working_days += 1
            current = date.fromordinal(current.toordinal() + 1)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "working_days": working_days,
            "holiday_excluded_days": 0,
            "makeup_workdays": 0,
            "available": False,
            "stale": False,
            "source": "weekday fallback",
        }

    def _capacity_for_period(
        self, start_date: str, end_date: str, standard_hours_per_day: Decimal
    ) -> tuple[Decimal, dict[str, Any]]:
        calendar = self._workday_summary(start_date, end_date)
        return Decimal(calendar["working_days"]) * standard_hours_per_day, calendar

    def employee_worktime_assessment(
        self,
        *,
        period: str = "month",
        month: str | None = None,
        year: str | None = None,
        employee_name: str | None = None,
    ) -> dict[str, Any]:
        baseline = self.get_worktime_baseline()
        start_date, end_date, label = _employee_period(period, month, year, self._now())
        with self.database.connect() as connection:
            latest_work_date_row = connection.execute(
                "SELECT MAX(work_date) AS latest_work_date FROM time_entries"
            ).fetchone()
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM time_entries
                    WHERE work_date >= ? AND work_date <= ?
                    ORDER BY employee_name, work_date DESC
                    """,
                    (start_date, end_date),
                ).fetchall()
            ]
        if employee_name:
            rows = [row for row in rows if row["employee_name"] == employee_name]
        standard_hours_per_day = Decimal(baseline["standard_hours_per_day"])
        baseline_hours, calendar = self._capacity_for_period(
            start_date, end_date, standard_hours_per_day
        )
        monitored_rows = _monitoring_time_entries(rows)
        grouped = _employee_worktime_rows(
            monitored_rows,
            baseline,
            baseline_hours,
            standard_hours_per_day,
            known_employees=rows,
        )
        return {
            "data": grouped,
            "summary": {
                "total_employees": len(grouped),
                "sufficient_count": sum(row["status"] == "工时充足" for row in grouped),
                "qualified_count": sum(row["status"] == "基本达标" for row in grouped),
                "attention_count": sum(row["status"] == "需要关注" for row in grouped),
                "critical_count": sum(row["status"] == "严重不足" for row in grouped),
            },
            "baseline": baseline,
            "meta": {
                "period": period,
                "period_label": label,
                "start_date": start_date,
                "end_date": end_date,
                "baseline_hours": _format_decimal(baseline_hours),
                "working_days": calendar["working_days"],
                "holiday_excluded_days": calendar["holiday_excluded_days"],
                "makeup_workdays": calendar["makeup_workdays"],
                "calendar_available": calendar["available"],
                "calendar_stale": calendar["stale"],
                "latest_work_month": (
                    latest_work_date_row["latest_work_date"][:7]
                    if latest_work_date_row and latest_work_date_row["latest_work_date"]
                    else None
                ),
                "excluded_internal_nonproject_entries": sum(
                    row["project_number"] == INTERNAL_NONPROJECT_CODE for row in rows
                ),
            },
        }

    def resource_operations(
        self,
        *,
        month: str | None = None,
        start_month: str | None = None,
        end_month: str | None = None,
        employee_name: str | None = None,
        cursor: str | None = None,
        page_size: int = 50,
        projects: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Provide a source-backed PMO capacity and project staffing view.

        ``projects`` are the project facts derived in the ontology (phase 2 moves this view there too).

        The recommendation deliberately uses only imported worktime, the shared
        worktime baseline, and project plan data.  Skills, departments and staff
        availability outside the source system are not assumed or fabricated.
        """
        baseline = self.get_worktime_baseline()
        with self.database.connect() as connection:
            latest_row = connection.execute(
                "SELECT MAX(work_date) AS latest_work_date FROM time_entries"
            ).fetchone()
        latest_month = (
            latest_row["latest_work_date"][:7]
            if latest_row and latest_row["latest_work_date"]
            else None
        )
        if (start_month is None) != (end_month is None):
            raise ValueError("start_month and end_month must be provided together")
        if month and start_month:
            raise ValueError("month cannot be combined with a date range")
        if start_month and end_month:
            start_date, _, normalized_start_month = _employee_period(
                "month", start_month, None, self._now()
            )
            _, end_date, normalized_end_month = _employee_period(
                "month", end_month, None, self._now()
            )
            if start_date > end_date:
                raise ValueError("start_month must not be after end_month")
            effective_month = normalized_end_month
            period_label = f"{normalized_start_month} 至 {normalized_end_month}"
            period_months = _plan_months(start_date, end_date)
        else:
            effective_month = month or latest_month or self._now()[:7]
            start_date, end_date, _ = _employee_period(
                "month", effective_month, None, self._now()
            )
            normalized_start_month = normalized_end_month = effective_month
            period_label = effective_month
            period_months = 1
        normalized_employee_name = (employee_name or "").strip()
        with self.database.connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM time_entries
                    WHERE work_date >= ? AND work_date <= ?
                      AND (? = '' OR employee_name LIKE ?)
                    ORDER BY employee_name, work_date DESC
                    """,
                    (start_date, end_date, normalized_employee_name, f"%{normalized_employee_name}%"),
                ).fetchall()
            ]

        standard_hours_per_day = Decimal(baseline["standard_hours_per_day"])
        baseline_hours, calendar = self._capacity_for_period(
            start_date, end_date, standard_hours_per_day
        )
        minimum_target = Decimal(baseline["minimum_utilization_percent"])
        attention_target = Decimal(baseline["attention_utilization_percent"])
        excellent_target = Decimal(baseline["excellent_utilization_percent"])
        overload_target = Decimal(baseline["overload_utilization_percent"])
        monitored_rows = _monitoring_time_entries(rows)
        assessment_rows = _employee_worktime_rows(
            monitored_rows,
            baseline,
            baseline_hours,
            standard_hours_per_day,
            known_employees=rows,
        )

        employee_projects: dict[str, dict[str, dict[str, Decimal]]] = {}
        for row in monitored_rows:
            employee = _employee_key(row)
            project = employee_projects.setdefault(employee, {}).setdefault(
                row["project_number"],
                {"reported_hours": Decimal("0"), "overtime_hours": Decimal("0")},
            )
            project["reported_hours"] += _total_entry_hours(row)
            project["overtime_hours"] += _overtime_entry_hours(row)

        internal_time_by_employee: dict[str, dict[str, Decimal | int]] = {}
        for row in _effort_entries(rows):
            if row["project_number"] != INTERNAL_NONPROJECT_CODE:
                continue
            item = internal_time_by_employee.setdefault(
                _employee_key(row),
                {"reported_hours": Decimal("0"), "overtime_hours": Decimal("0"), "entry_count": 0},
            )
            item["reported_hours"] += _total_entry_hours(row)
            item["overtime_hours"] += _overtime_entry_hours(row)
            item["entry_count"] += 1

        all_projects = projects
        project_names = {
            project["project_number"]: project["subject"] for project in all_projects
        }
        employees: list[dict[str, Any]] = []
        total_available_days = Decimal("0")
        available_count = high_load_count = overloaded_count = 0
        for assessment in assessment_rows:
            utilization = Decimal(assessment["utilization_percent"])
            reported_hours = Decimal(assessment["reported_hours"])
            internal_time = internal_time_by_employee.get(
                _employee_key(assessment),
                {"reported_hours": Decimal("0"), "overtime_hours": Decimal("0"), "entry_count": 0},
            )
            internal_reported_hours = Decimal(internal_time["reported_hours"])
            internal_overtime_hours = Decimal(internal_time["overtime_hours"])
            if utilization <= attention_target:
                allocation_status = "可优先调剂"
                available_hours = max(
                    baseline_hours * minimum_target / Decimal("100") - reported_hours,
                    Decimal("0"),
                )
            elif utilization <= minimum_target:
                allocation_status = "可适度支持"
                available_hours = max(
                    baseline_hours * minimum_target / Decimal("100") - reported_hours,
                    Decimal("0"),
                )
            elif utilization > overload_target:
                allocation_status = "负荷过高"
                available_hours = Decimal("0")
                high_load_count += 1
                overloaded_count += 1
            elif utilization > excellent_target:
                allocation_status = "负荷较高"
                available_hours = Decimal("0")
                high_load_count += 1
            else:
                allocation_status = "安排基本均衡"
                available_hours = Decimal("0")

            # TSG999 是内部非项目工时：不影响项目负荷，但已占用的内部工时可
            # 直接改投项目。取“内部可调剂工时”和常规产能缺口的较大值，避免重复计算。
            # 负荷较高或过高的人员不再对外调剂，即使有内部工时。
            if allocation_status not in {"负荷较高", "负荷过高"}:
                available_hours = max(available_hours, internal_reported_hours)
            if available_hours > 0:
                available_count += 1

            assignments = []
            for project_number, values in employee_projects.get(
                _employee_key(assessment), {}
            ).items():
                assignments.append(
                    {
                        "project_number": project_number,
                        "project_name": project_names.get(project_number, project_number),
                        "reported_person_days": _format_decimal(
                            values["reported_hours"] / standard_hours_per_day
                            if standard_hours_per_day else Decimal("0")
                        ),
                        "overtime_person_days": _format_decimal(
                            values["overtime_hours"] / standard_hours_per_day
                            if standard_hours_per_day else Decimal("0")
                        ),
                    }
                )
            if internal_reported_hours > 0:
                assignments.append(
                    {
                        "project_number": INTERNAL_NONPROJECT_CODE,
                        "project_name": "内部非项目工时（可调剂）",
                        "reported_person_days": _format_decimal(
                            internal_reported_hours / standard_hours_per_day
                            if standard_hours_per_day else Decimal("0")
                        ),
                        "overtime_person_days": _format_decimal(
                            internal_overtime_hours / standard_hours_per_day
                            if standard_hours_per_day else Decimal("0")
                        ),
                        "is_internal_nonproject": True,
                    }
                )
            assignments.sort(
                key=lambda item: (
                    not item.get("is_internal_nonproject", False),
                    -Decimal(item["reported_person_days"]),
                    item["project_number"],
                )
            )
            available_days = (
                available_hours / standard_hours_per_day
                if standard_hours_per_day else Decimal("0")
            )
            total_available_days += available_days
            employees.append(
                {
                    **assessment,
                    "allocation_status": allocation_status,
                    "available_person_days": _format_decimal(available_days),
                    "internal_nonproject_person_days": _format_decimal(
                        internal_reported_hours / standard_hours_per_day
                        if standard_hours_per_day else Decimal("0")
                    ),
                    "internal_nonproject_entry_count": internal_time["entry_count"],
                    "assignments": assignments,
                }
            )
        employees.sort(
            key=lambda item: (
                {"负荷过高": 0, "负荷较高": 1, "可优先调剂": 2, "可适度支持": 3, "安排基本均衡": 4}[item["allocation_status"]],
                item["employee_name"],
            )
        )
        page, next_cursor = _cursor_page(
            employees,
            cursor=cursor,
            page_size=page_size,
            kind="resource-operations",
            key=lambda item: (
                {"负荷过高": 0, "负荷较高": 1, "可优先调剂": 2, "可适度支持": 3, "安排基本均衡": 4}[item["allocation_status"]],
                item["employee_name"],
                item["employee_id"],
            ),
            scope=_cursor_scope(
                month=month,
                start_month=start_month,
                end_month=end_month,
                employee_name=normalized_employee_name,
            ),
        )
        return {
            "data": page,
            "summary": {
                "total_employees": len(employees),
                "available_count": available_count,
                "high_load_count": high_load_count,
                "overloaded_count": overloaded_count,
                "available_person_days": _format_decimal(total_available_days),
            },
            "baseline": baseline,
            "meta": {
                "month": effective_month,
                "period_label": period_label,
                "start_month": normalized_start_month,
                "end_month": normalized_end_month,
                "employee_name": normalized_employee_name or None,
                "start_date": start_date,
                "end_date": end_date,
                "baseline_hours": _format_decimal(baseline_hours),
                "working_days": calendar["working_days"],
                "holiday_excluded_days": calendar["holiday_excluded_days"],
                "makeup_workdays": calendar["makeup_workdays"],
                "calendar_available": calendar["available"],
                "calendar_stale": calendar["stale"],
                "latest_work_month": latest_month,
                "excluded_internal_nonproject_entries": sum(
                    row["project_number"] == INTERNAL_NONPROJECT_CODE for row in rows
                ),
                "internal_nonproject_project_code": INTERNAL_NONPROJECT_CODE,
                "recommendation_limitations": "TSG999 为内部非项目工时，不计入资源监控；其余建议仅基于导入工时、工时基线与项目计划，未包含员工技能、角色、请假或外部排期。",
                "page_size": page_size,
                "next_cursor": next_cursor,
            },
        }

    def upsert_settings(self, project_number: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        current = self._settings().get(project_number, {})
        planned_start = _optional_iso_date(
            payload.get("planned_start", current.get("planned_start")), "planned_start"
        )
        planned_end = _optional_iso_date(
            payload.get("planned_end", current.get("planned_end")), "planned_end"
        )
        rules.ensure(rules.date_order(planned_start, planned_end, "计划结束日期"))
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
            "planned_start": planned_start,
            "planned_end": planned_end,
            "project_manager": payload.get(
                "project_manager", current.get("project_manager")
            ),
            "notes": payload.get("notes", current.get("notes")),
        }
        rules.ensure(rules.capped_budget(values["contract_type"], values["planned_person_days"]))
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
        # Callers that need the project view read it from the ontology.
        return {"project_number": project_number}

    def import_time_entries(self, entries: list[dict[str, Any]]) -> dict[str, int]:
        now = self._now()
        imported = 0
        updated = 0
        unchanged = 0
        with self.database.connect() as connection:
            for entry in entries:
                hours = _decimal_text(entry["hours"])
                overtime_hours = _decimal_text(entry.get("overtime_hours", "0"))
                approved_hours = _decimal_text(entry.get("approved_hours", "0"))
                if (
                    Decimal(hours) < 0
                    or Decimal(overtime_hours) < 0
                    or Decimal(approved_hours) < 0
                    or Decimal(hours) + Decimal(overtime_hours) > 24
                    or Decimal(approved_hours) > 24
                ):
                    raise ValueError("total daily hours must be between 0 and 24")
                # Excel exports do not provide a source-row ID.  Check their
                # semantic identity before insert so historical generated IDs
                # that differ only by display format or workflow status cannot
                # inflate the same employee's project effort.  API imports are
                # excluded because their caller-supplied external IDs can
                # distinguish legitimately identical source rows.
                if str(entry["external_id"]).startswith("TIMESHEET-XLSX-"):
                    existing = connection.execute(
                        """
                        SELECT external_id, approval_status FROM time_entries
                        WHERE external_id LIKE 'TIMESHEET-XLSX-%'
                          AND employee_id=? AND project_number=? AND work_date=?
                          AND hours=? AND overtime_hours=? AND work_content=?
                        LIMIT 1
                        """,
                        (
                            entry["employee_id"], entry["project_number"],
                            entry["work_date"], hours, overtime_hours,
                            entry["work_content"],
                        ),
                    ).fetchone()
                    if existing:
                        # The workflow status may only move forward (submitted → approved / rejected):
                        # without a reliable export time an older file must not undo an approval.
                        if (
                            existing["approval_status"] not in FINAL_APPROVAL_STATUSES
                            and entry["approval_status"] in FINAL_APPROVAL_STATUSES
                        ):
                            connection.execute(
                                """
                                UPDATE time_entries
                                SET approval_status=?, approved_hours=?, imported_at=?
                                WHERE external_id=?
                                """,
                                (entry["approval_status"], approved_hours, now, existing["external_id"]),
                            )
                            updated += 1
                        else:
                            unchanged += 1
                        continue
                existing_version = connection.execute(
                    "SELECT source_updated_at FROM time_entries WHERE external_id = ?",
                    (entry["external_id"],),
                ).fetchone()
                if existing_version and not _is_newer_source_version(
                    entry["source_updated_at"], existing_version["source_updated_at"]
                ):
                    unchanged += 1
                    continue
                cursor = connection.execute(
                    """
                    INSERT INTO time_entries(
                        external_id, project_number, employee_id, employee_name,
                        work_date, hours, overtime_hours, approved_hours, approval_status,
                        work_content, source_updated_at, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(external_id) DO UPDATE SET
                        project_number=excluded.project_number,
                        employee_id=excluded.employee_id,
                        employee_name=excluded.employee_name,
                        work_date=excluded.work_date,
                        hours=excluded.hours,
                        overtime_hours=excluded.overtime_hours,
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
                        overtime_hours,
                        approved_hours,
                        entry["approval_status"],
                        entry["work_content"],
                        entry["source_updated_at"],
                        now,
                    ),
                )
                if existing_version:
                    updated += 1
                elif cursor.rowcount:
                    imported += 1
                else:
                    unchanged += 1
            connection.commit()
        return {"imported": imported, "updated": updated, "unchanged": unchanged}

    def _ensure_worktime_baseline(self) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT config_id FROM worktime_baseline_config WHERE config_id = 'default'"
            ).fetchone()
            if row:
                return
            connection.execute(
                """
                INSERT INTO worktime_baseline_config(
                    config_id, standard_work_days_per_year, standard_hours_per_day,
                    annual_baseline_hours, effective_baseline_hours,
                    monthly_baseline_hours, quarterly_baseline_hours,
                    minimum_utilization_percent, excellent_utilization_percent,
                    attention_utilization_percent, overload_utilization_percent,
                    notes, updated_at
                ) VALUES (
                    'default', '230.00', '8.00', '1840.00', '1564.00',
                    '153.00', '460.00', '75.00', '90.00', '60.00', '100.00',
                    '默认工时基线参数，可在页面人工维护。', ?
                )
                """,
                (self._now(),),
            )
            connection.commit()

    def create_milestone(self, project_number: str, payload: dict[str, Any]) -> dict[str, Any]:
        milestone_id = "ms_" + uuid.uuid4().hex[:16]
        planned_end = _optional_iso_date(
            payload.get("planned_end") or payload.get("planned_date"), "planned_end"
        )
        if planned_end is None:
            raise ValueError("planned_end is required")
        planned_start = _optional_iso_date(
            payload.get("planned_start") or planned_end, "planned_start"
        )
        rules.ensure(rules.date_order(planned_start, planned_end, "计划完成日期"))
        payment_term_id = payload.get("payment_term_id") or None
        is_payment_node = bool(payment_term_id) or bool(payload.get("is_payment_node", False))
        payment_date = payload.get("payment_date")
        payment_amount = payload.get("payment_amount")
        if is_payment_node and not payment_term_id and (not payment_date or payment_amount in (None, "")):
            raise ValueError("payment_date and payment_amount are required for payment milestones")
        payment_node_id = "pay_" + uuid.uuid4().hex[:16] if is_payment_node else None
        with self.database.connect() as connection:
            payment_term = self._payment_term_for_link(
                connection, project_number, payment_term_id
            )
            if payment_term:
                payment_node_id = payment_term.get("payment_node_id") or payment_node_id
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
            if payment_term:
                self._upsert_payment_event_for_term(
                    connection=connection,
                    project_number=project_number,
                    milestone_id=milestone_id,
                    payment_node_id=payment_node_id,
                    payment_term=payment_term,
                    planned_date=planned_end,
                )
            elif is_payment_node:
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
        rules.ensure(rules.locked("milestone", current["status"]))
        if payload.get("status") == "completed":
            raise ValueError("use the confirm endpoint to complete a milestone")
        values = {**current, **payload}
        planned_end = _optional_iso_date(
            values.get("planned_end") or values.get("planned_date"), "planned_end"
        )
        if planned_end is None:
            raise ValueError("planned_end is required")
        planned_start = _optional_iso_date(
            values.get("planned_start") or planned_end, "planned_start"
        )
        rules.ensure(rules.date_order(planned_start, planned_end, "计划完成日期"))
        term_supplied = "payment_term_id" in payload
        payment_term_id = payload.get("payment_term_id") if term_supplied else None
        payment_node_id = current.get("payment_node_id")
        payment_date = payload.get("payment_date")
        payment_amount = payload.get("payment_amount")
        with self.database.connect() as connection:
            previous_term = connection.execute(
                "SELECT * FROM payment_terms WHERE linked_milestone_id = ?",
                (milestone_id,),
            ).fetchone()
            payment_term = self._payment_term_for_link(
                connection, project_number, payment_term_id, milestone_id
            ) if payment_term_id else None
            settled = False
            if not term_supplied and previous_term:
                # A linked term keeps driving the payment node, so its date follows the edited planned end;
                # a payment already received is left as it was.
                node = connection.execute(
                    "SELECT status FROM payment_nodes WHERE payment_node_id = ?", (payment_node_id,)
                ).fetchone() if payment_node_id else None
                settled = bool(node and node["status"] == "paid")
                if not settled:
                    payment_term = self._payment_term_for_link(
                        connection, project_number, previous_term["payment_term_id"], milestone_id
                    )
            if term_supplied and payment_node_id and (
                not previous_term or previous_term["payment_term_id"] != payment_term_id
            ):
                self._remove_milestone_payment_event(
                    connection, milestone_id, payment_node_id
                )
                payment_node_id = None
            if payment_term:
                payment_node_id = payment_term.get("payment_node_id") or payment_node_id or (
                    "pay_" + uuid.uuid4().hex[:16]
                )
                is_payment_node = True
            elif term_supplied:
                is_payment_node = False
            elif settled:
                is_payment_node = True
            else:
                is_payment_node = bool(values.get("is_payment_node"))
                if is_payment_node and not payment_node_id:
                    if not payment_date or payment_amount in (None, ""):
                        raise ValueError("payment_date and payment_amount are required for payment milestones")
                    payment_node_id = "pay_" + uuid.uuid4().hex[:16]
                elif not is_payment_node and payment_node_id:
                    self._remove_milestone_payment_event(
                        connection, milestone_id, payment_node_id
                    )
                    payment_node_id = None
            connection.execute(
                """
                UPDATE milestones SET sequence_no=?, name=?, planned_date=?,
                    planned_start=?, planned_end=?, status=?, owner=?, deliverable=?,
                    is_payment_node=?, payment_node_id=?, notes=?, updated_at=?
                WHERE milestone_id=? AND project_number=?
                """,
                (
                    values.get("sequence_no"), values["name"], planned_end,
                    planned_start, planned_end,
                    values.get("status", "planned"), values.get("owner"),
                    values.get("deliverable"), 1 if is_payment_node else 0,
                    payment_node_id, values.get("notes"), self._now(),
                    milestone_id, project_number,
                ),
            )
            if payment_term:
                self._upsert_payment_event_for_term(
                    connection=connection,
                    project_number=project_number,
                    milestone_id=milestone_id,
                    payment_node_id=payment_node_id,
                    payment_term=payment_term,
                    planned_date=planned_end,
                )
            elif is_payment_node and not settled:
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

    def delete_milestone(self, project_number: str, milestone_id: str) -> dict[str, Any]:
        current = self._row_by_project_id(
            "milestones", "milestone_id", milestone_id, project_number
        )
        if current is None:
            raise KeyError("milestone not found")
        rules.ensure(rules.locked("milestone", current["status"]))
        payment_node_id = current.get("payment_node_id")
        with self.database.connect() as connection:
            if payment_node_id:
                payment = connection.execute(
                    "SELECT status FROM payment_nodes WHERE payment_node_id = ?",
                    (payment_node_id,),
                ).fetchone()
                rules.ensure(rules.linked_payment_unpaid(payment["status"] if payment else None))
            connection.execute(
                "DELETE FROM milestones WHERE milestone_id = ? AND project_number = ?",
                (milestone_id, project_number),
            )
            if payment_node_id:
                connection.execute(
                    "UPDATE payment_terms SET linked_milestone_id=NULL, payment_node_id=NULL, updated_at=? "
                    "WHERE linked_milestone_id=?",
                    (self._now(), milestone_id),
                )
                connection.execute(
                    "DELETE FROM payment_nodes WHERE payment_node_id = ?",
                    (payment_node_id,),
                )
            connection.commit()
        # Callers that need the project view read it from the ontology.
        return {"project_number": project_number}

    def create_payment_term(
        self, project_number: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        values = self._validated_payment_term(payload)
        payment_term_id = "term_" + uuid.uuid4().hex[:16]
        now = self._now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO payment_terms(
                    payment_term_id, project_number, name, payment_ratio,
                    planned_amount, notes, linked_milestone_id, payment_node_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    payment_term_id, project_number, values["name"],
                    values["payment_ratio"], values["planned_amount"],
                    values["notes"], now, now,
                ),
            )
            connection.commit()
        return self._row_by_id("payment_terms", "payment_term_id", payment_term_id)

    def update_payment_term(
        self, project_number: str, payment_term_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        current = self._row_by_project_id(
            "payment_terms", "payment_term_id", payment_term_id, project_number
        )
        if current is None:
            raise KeyError("payment term not found")
        values = self._validated_payment_term({**current, **payload})
        with self.database.connect() as connection:
            if current.get("payment_node_id"):
                payment = connection.execute(
                    "SELECT status FROM payment_nodes WHERE payment_node_id=?", (current["payment_node_id"],)
                ).fetchone()
                rules.ensure(rules.term_locked(payment["status"] if payment else None))
            connection.execute(
                """
                UPDATE payment_terms SET name=?, payment_ratio=?, planned_amount=?,
                    notes=?, updated_at=? WHERE payment_term_id=? AND project_number=?
                """,
                (
                    values["name"], values["payment_ratio"], values["planned_amount"],
                    values["notes"], self._now(), payment_term_id, project_number,
                ),
            )
            if current.get("payment_node_id"):
                payment = connection.execute(
                    "SELECT status FROM payment_nodes WHERE payment_node_id=?",
                    (current["payment_node_id"],),
                ).fetchone()
                if payment and payment["status"] != "paid":
                    connection.execute(
                        """
                        UPDATE payment_nodes SET name=?, planned_amount=?, notes=?, updated_at=?
                        WHERE payment_node_id=?
                        """,
                        (
                            values["name"], values["planned_amount"], values["notes"],
                            self._now(), current["payment_node_id"],
                        ),
                    )
            connection.commit()
        return self._row_by_id("payment_terms", "payment_term_id", payment_term_id)

    def delete_payment_term(self, project_number: str, payment_term_id: str) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM payment_terms WHERE payment_term_id=? AND project_number=?",
                (payment_term_id, project_number),
            ).fetchone()
            if row is None:
                raise KeyError("payment term not found")
            rules.ensure(rules.term_unlinked(row["linked_milestone_id"]))
            connection.execute(
                "DELETE FROM payment_terms WHERE payment_term_id=?", (payment_term_id,)
            )
            connection.commit()

    def _validated_payment_term(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("payment term name is required")
        amount = payload.get("planned_amount")
        if amount in (None, ""):
            raise ValueError("planned_amount is required")
        amount_text = _decimal_text(amount)
        rules.ensure(rules.non_negative(amount_text))
        ratio = payload.get("payment_ratio")
        ratio_text = None
        if ratio not in (None, ""):
            ratio_text = _decimal_text(ratio)
            rules.ensure(rules.payment_ratio(ratio_text))
        return {
            "name": name,
            "payment_ratio": ratio_text,
            "planned_amount": amount_text,
            "notes": payload.get("notes"),
        }

    def _payment_term_for_link(
        self,
        connection: Any,
        project_number: str,
        payment_term_id: str | None,
        milestone_id: str | None = None,
    ) -> dict[str, Any] | None:
        if not payment_term_id:
            return None
        row = connection.execute(
            "SELECT * FROM payment_terms WHERE payment_term_id=? AND project_number=?",
            (payment_term_id, project_number),
        ).fetchone()
        if row is None:
            raise ValueError("payment term not found")
        rules.ensure(rules.term_available(row["linked_milestone_id"], milestone_id))
        return dict(row)

    def _upsert_payment_event_for_term(
        self,
        *,
        connection: Any,
        project_number: str,
        milestone_id: str,
        payment_node_id: str,
        payment_term: dict[str, Any],
        planned_date: str,
    ) -> None:
        existing = connection.execute(
            "SELECT status FROM payment_nodes WHERE payment_node_id=?",
            (payment_node_id,),
        ).fetchone()
        rules.ensure(rules.locked("paymentNode", existing["status"] if existing else None))
        if existing:
            connection.execute(
                """
                UPDATE payment_nodes SET name=?, planned_date=?, planned_amount=?, notes=?,
                    payment_term_id=?, updated_at=? WHERE payment_node_id=?
                """,
                (
                    payment_term["name"], planned_date, payment_term["planned_amount"],
                    payment_term.get("notes"), payment_term["payment_term_id"],
                    self._now(), payment_node_id,
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO payment_nodes(
                    payment_node_id, project_number, name, planned_date, planned_amount,
                    actual_amount, status, notes, updated_at, payment_term_id
                ) VALUES (?, ?, ?, ?, ?, '0.00', 'planned', ?, ?, ?)
                """,
                (
                    payment_node_id, project_number, payment_term["name"], planned_date,
                    payment_term["planned_amount"], payment_term.get("notes"), self._now(),
                    payment_term["payment_term_id"],
                ),
            )
        connection.execute(
            """
            UPDATE payment_terms SET linked_milestone_id=?, payment_node_id=?, updated_at=?
            WHERE payment_term_id=?
            """,
            (milestone_id, payment_node_id, self._now(), payment_term["payment_term_id"]),
        )

    def _remove_milestone_payment_event(
        self, connection: Any, milestone_id: str, payment_node_id: str
    ) -> None:
        payment = connection.execute(
            "SELECT status FROM payment_nodes WHERE payment_node_id=?",
            (payment_node_id,),
        ).fetchone()
        rules.ensure(rules.locked("paymentNode", payment["status"] if payment else None))
        connection.execute(
            "UPDATE payment_terms SET linked_milestone_id=NULL, payment_node_id=NULL, updated_at=? "
            "WHERE linked_milestone_id=?",
            (self._now(), milestone_id),
        )
        connection.execute(
            "DELETE FROM payment_nodes WHERE payment_node_id=?", (payment_node_id,)
        )

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

    def update_payment_node(
        self, project_number: str, payment_node_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM payment_nodes WHERE payment_node_id=? AND project_number=?",
                (payment_node_id, project_number),
            ).fetchone()
            if row is None:
                raise KeyError("payment node not found")
            rules.ensure(rules.locked("paymentNode", row["status"]))
            connection.execute(
                """
                UPDATE payment_nodes SET name=?, planned_date=?, planned_amount=?,
                    notes=?, updated_at=? WHERE payment_node_id=?
                """,
                (
                    payload.get("name") or row["name"],
                    payload.get("planned_date") or row["planned_date"],
                    _decimal_text(payload.get("planned_amount", row["planned_amount"])),
                    payload.get("notes", row["notes"]),
                    self._now(),
                    payment_node_id,
                ),
            )
            connection.commit()
        return self._row_by_id("payment_nodes", "payment_node_id", payment_node_id)

    def delete_payment_node(self, project_number: str, payment_node_id: str) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM payment_nodes WHERE payment_node_id=? AND project_number=?",
                (payment_node_id, project_number),
            ).fetchone()
            if row is None:
                raise KeyError("payment node not found")
            rules.ensure(rules.locked("paymentNode", row["status"]))
            linked_milestone = connection.execute(
                "SELECT milestone_id FROM milestones WHERE payment_node_id=?",
                (payment_node_id,),
            ).fetchone()
            rules.ensure(rules.node_not_milestone_managed(linked_milestone["milestone_id"] if linked_milestone else None))
            connection.execute(
                "DELETE FROM payment_nodes WHERE payment_node_id=?",
                (payment_node_id,),
            )
            connection.commit()

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

    def update_intervention_status(
        self, project_number: str, intervention_id: str, status: str, notes: str | None = None
    ) -> dict[str, Any]:
        current = self._row_by_project_id(
            "interventions", "intervention_id", intervention_id, project_number
        )
        if current is None:
            raise KeyError("intervention not found")
        rules.ensure(rules.locked("intervention", current["status"]))
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE interventions SET status=?, notes=?, updated_at=?
                WHERE intervention_id=? AND project_number=?
                """,
                (
                    status,
                    notes if notes is not None else current["notes"],
                    self._now(),
                    intervention_id,
                    project_number,
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

    def remove_legacy_demo_time_entries(self) -> int:
        """Remove old seeded time entries so the worktime view only reflects uploads."""
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM time_entries WHERE external_id LIKE 'DEMO-TIME-%'"
            )
            connection.commit()
        return cursor.rowcount

    def cleanup_legacy_orphaned_maintenance_once(self) -> dict[str, int]:
        """Remove legacy manual demo records that have no active CRM project.

        Project master views are sourced from the active CRM import.  Historic
        worktime rows are intentionally retained as imported source data: when
        their CRM project is uploaded later they become visible automatically.
        The cleanup only removes old manual configuration/demo data which would
        otherwise create unsupported project cards.
        """
        cleanup_key = "legacy_orphaned_project_maintenance_cleanup_v1"
        snapshot = self.records.export_records()
        if not snapshot:
            return {"project_settings": 0, "milestones": 0, "payment_nodes": 0, "payment_terms": 0, "interventions": 0}

        crm_project_numbers = {
            record.get("project_number") or _project_number_from_subject(record.get("subject") or "")
            for record in snapshot["records"]
        }
        crm_project_numbers.discard(None)
        crm_project_numbers.discard("")
        if not crm_project_numbers:
            return {"project_settings": 0, "milestones": 0, "payment_nodes": 0, "payment_terms": 0, "interventions": 0}

        with self.database.connect() as connection:
            already_cleaned = connection.execute(
                "SELECT 1 FROM service_state WHERE key = ?", (cleanup_key,)
            ).fetchone()
            if already_cleaned:
                return {"project_settings": 0, "milestones": 0, "payment_nodes": 0, "payment_terms": 0, "interventions": 0}

            placeholders = ", ".join("?" for _ in crm_project_numbers)
            deleted: dict[str, int] = {}
            for table in ("project_settings", "milestones", "payment_nodes", "payment_terms", "interventions"):
                cursor = connection.execute(
                    f"DELETE FROM {table} WHERE project_number NOT IN ({placeholders})",
                    tuple(sorted(crm_project_numbers)),
                )
                deleted[table] = cursor.rowcount
            connection.execute(
                "INSERT INTO service_state(key, value) VALUES (?, ?)",
                (cleanup_key, self._now()),
            )
            connection.commit()
        return deleted


    # ------------------------------------------------------------------ base data for the ontology
    def project_sources(self, project_numbers: set[str] | None = None) -> list[dict[str, Any]]:
        """Raw per-project bundles: CRM rows, maintained settings and child records.

        This is all the base program hands over.  Every project fact computed from these bundles
        (risk, health, budget, collections, todos) is derived in the ontology (ontology/derivations).
        """
        snapshot = self.records.export_records(project_numbers)
        crm_records = snapshot["records"] if snapshot else []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in crm_records:
            project = record.get("project_number") or _project_number_from_subject(
                record.get("subject") or ""
            )
            if project:
                grouped.setdefault(project, []).append(record)

        settings = self._settings(project_numbers)
        time_entries = self._rows_grouped(
            "time_entries", "project_number", "work_date DESC", project_numbers
        )
        milestones = self._rows_grouped(
            "milestones", "project_number", "planned_date", project_numbers
        )
        payments = self._rows_grouped(
            "payment_nodes", "project_number", "planned_date", project_numbers
        )
        payment_terms = self._rows_grouped(
            "payment_terms", "project_number", "created_at", project_numbers
        )
        interventions = self._rows_grouped(
            "interventions", "project_number", "updated_at DESC", project_numbers
        )
        if self.require_crm_project_match:
            # The active CRM import is the project-master source of truth.
            # Unmatched worktime stays as raw source data until its CRM record
            # is uploaded, but never creates an unlinked project card.
            project_numbers = set(grouped)
        else:
            # Compatibility mode is reserved for isolated data-import tests.
            project_numbers = (
                set(grouped) | set(settings) | set(time_entries) | set(milestones)
                | set(payments) | set(interventions)
            )
        project_numbers.discard(INTERNAL_NONPROJECT_CODE)
        return [
            {
                "project_number": project_number,
                "crm_records": grouped.get(project_number, []),
                "settings": settings.get(project_number, {}),
                "time_entries": time_entries.get(project_number, []),
                "milestones": milestones.get(project_number, []),
                "payment_nodes": payments.get(project_number, []),
                "payment_terms": payment_terms.get(project_number, []),
                "interventions": interventions.get(project_number, []),
            }
            for project_number in project_numbers
        ]

    def today(self) -> date:
        return datetime.now(ZoneInfo(self.timezone)).date()

    def assistant_candidate_codes(
        self, query: str, *, project_limit: int = 16, record_limit: int = 24
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Bounded CRM search for PM Assistant: matching project codes and CRM rows.

        Project facts for the codes come from the ontology; this only searches base data.
        """
        records_by_id: dict[str, dict[str, Any]] = {}
        for term in _assistant_search_terms(query):
            for record in self.records.assistant_record_candidates(term, limit=record_limit):
                records_by_id.setdefault(record["record_id"], record)
                if len(records_by_id) >= record_limit:
                    break
            if len(records_by_id) >= record_limit:
                break
        records = list(records_by_id.values())
        project_numbers = {
            str(record["project_number"])
            for record in records
            if record.get("project_number")
        }
        if not project_numbers:
            records = self.records.assistant_record_candidates("", limit=record_limit)
            project_numbers = {
                str(record["project_number"])
                for record in records
                if record.get("project_number")
            }
        project_numbers.discard(INTERNAL_NONPROJECT_CODE)
        return sorted(project_numbers)[:project_limit], records

    def _crm_search_terms_by_project(self) -> dict[str, list[str]]:
        """Return all CRM names usable for the project-master fuzzy search.

        A project can contain several imported CRM rows (for example a contract
        plus its original opportunity).  The list view selects a single primary
        row for display, but search must still find the project by any linked
        project name or customer name.
        """
        snapshot = self.records.export_records()
        records = snapshot["records"] if snapshot else []
        terms_by_project: dict[str, list[str]] = {}
        for record in records:
            project_number = record.get("project_number") or _project_number_from_subject(
                record.get("subject") or ""
            )
            if not project_number:
                continue
            terms = terms_by_project.setdefault(project_number, [])
            terms.extend(
                value
                for value in (
                    record.get("project_number"),
                    record.get("subject"),
                    record.get("customer"),
                )
                if value
            )
        return terms_by_project


    def _settings(
        self, project_numbers: set[str] | None = None
    ) -> dict[str, dict[str, Any]]:
        with self.database.connect() as connection:
            if project_numbers is None:
                rows = connection.execute("SELECT * FROM project_settings").fetchall()
            elif not project_numbers:
                rows = []
            else:
                placeholders = ", ".join("?" for _ in project_numbers)
                rows = connection.execute(
                    f"SELECT * FROM project_settings WHERE project_number IN ({placeholders})",
                    tuple(sorted(project_numbers)),
                ).fetchall()
        return {row["project_number"]: dict(row) for row in rows}

    def _rows_grouped(
        self,
        table: str,
        key: str,
        order_by: str,
        project_numbers: set[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        with self.database.connect() as connection:
            if project_numbers is None:
                rows = connection.execute(
                    f"SELECT * FROM {table} ORDER BY {order_by}"
                ).fetchall()
            elif not project_numbers:
                rows = []
            else:
                placeholders = ", ".join("?" for _ in project_numbers)
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE project_number IN ({placeholders}) ORDER BY {order_by}",
                    tuple(sorted(project_numbers)),
                ).fetchall()
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
        milestone["payment_term_id"] = payment.get("payment_term_id") if payment else None
        return milestone

    def _now(self) -> str:
        return datetime.now(ZoneInfo(self.timezone)).isoformat(timespec="seconds")


def _cursor_page(
    rows: list[dict[str, Any]],
    *,
    cursor: str | None,
    page_size: int,
    kind: str,
    key: Any,
    scope: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Apply keyset pagination to an already sorted analytical result set."""
    try:
        cursor_data = decode_cursor(cursor, kind)
    except CursorError:
        raise
    start = 0
    if cursor_data:
        if cursor_data.get("scope") != scope:
            raise CursorError("cursor does not match the current filters")
        last_key = tuple(cursor_data.get("key", []))
        for index, row in enumerate(rows):
            if tuple(key(row)) > last_key:
                start = index
                break
        else:
            return [], None
    page = rows[start : start + page_size]
    next_cursor = (
        encode_cursor(kind, key=list(key(page[-1])), scope=scope)
        if start + page_size < len(rows) and page
        else None
    )
    return page, next_cursor


def _assistant_search_terms(question: str) -> list[str]:
    """Extract small searchable terms from a natural-language PM question."""
    terms = re.findall(r"[A-Za-z0-9_-]{2,}", question)
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", question):
        terms.append(phrase)
        terms.extend(phrase[index : index + 2] for index in range(len(phrase) - 1))
    return list(dict.fromkeys(term for term in terms if len(term) >= 2))[:8]


def _cursor_scope(**values: Any) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _contracts_are_source_closed(contracts: list[dict[str, Any]]) -> bool:
    """Return whether every imported contract row is explicitly terminal.

    The CRM source's status is retained verbatim for audit.  Project views no
    longer expose a separate contract-status field, but a completed source
    contract with later worktime must still be flagged as extra investment.
    """
    terminal_statuses = {"结束", "已结束", "完成", "已完成", "关闭", "已关闭", "终止", "已终止"}
    return bool(contracts) and all(
        str(contract.get("status") or "").strip() in terminal_statuses
        for contract in contracts
    )


def _decimal_text(value: Any) -> str:
    return _format_decimal(Decimal(str(value)))


def _optional_iso_date(value: Any, field: str) -> str | None:
    """Normalize manually maintained schedule dates before health calculations."""
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)") from exc


def _overtime_entry_hours(row: dict[str, Any]) -> Decimal:
    return Decimal(row.get("overtime_hours") or "0")


def _total_entry_hours(row: dict[str, Any]) -> Decimal:
    return Decimal(row["hours"]) + _overtime_entry_hours(row)


def _signed_year(record: dict[str, Any]) -> int | None:
    """Signing year of a CRM contract (its 签约时间); opportunities and undated contracts have none."""
    signed = record.get("signed_at")
    if record.get("data_type") != "contract_order" or not signed:
        return None
    return int(str(signed)[:4])


def _counts_as_effort(row: dict[str, Any]) -> bool:
    return row.get("approval_status") in EFFORT_APPROVAL_STATUSES


def _effort_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if _counts_as_effort(row)]


def _project_number_from_subject(subject: str) -> str | None:
    match = re.match(r"^([A-Za-z]{2,}[A-Za-z0-9-]*)[-—–_]", subject.strip())
    return match.group(1).upper() if match else None


# Operating-policy parameters for resource health and the department review (the economics ones are paused
# with person-day economics but kept, so the settings page and ontology action still agree):
# name → (minimum, maximum, label, whole number). Shared by the settings page and the ontology action.
POLICY_RANGES: dict[str, tuple[Decimal, Decimal, str, bool]] = {
    "default_tax_rate_percent": (Decimal("0"), Decimal("99.99"), "默认税率", False),
    "default_target_margin_percent": (Decimal("0"), Decimal("99.99"), "默认目标毛利率", False),
    "cost_ahead_warning_points": (Decimal("0.01"), Decimal("500"), "成本超前预警线", False),
    "overtime_share_attention_percent": (Decimal("0.01"), Decimal("99.99"), "加班占比关注线", False),
    "overload_consecutive_months": (Decimal("1"), Decimal("12"), "连续过载月数", True),
    "max_concurrent_projects": (Decimal("1"), Decimal("50"), "同期项目数上限", True),
    "backlog_coverage_percent": (Decimal("0.01"), Decimal("500"), "在手合同覆盖线", False),
    "plan_lag_tolerance_points": (Decimal("0"), Decimal("100"), "年度计划落后容差", False),
    "control_cost_exceeded_projects": (Decimal("1"), Decimal("100"), "控制成本的超标项目数", True),
}


def _validate_worktime_baseline(values: dict[str, Any]) -> None:
    positive_fields = (
        "standard_work_days_per_year",
        "standard_hours_per_day",
        "annual_baseline_hours",
        "effective_baseline_hours",
        "monthly_baseline_hours",
        "quarterly_baseline_hours",
    )
    for field in positive_fields:
        if Decimal(values[field]) <= 0:
            raise ValueError(f"{field} must be greater than 0")
    # Same ranges as the ontology action's policyValuesValid criterion, so both write paths agree.
    if Decimal(values["standard_hours_per_day"]) > 24:
        raise ValueError("standard_hours_per_day must be no more than 24")
    if not Decimal("1") <= Decimal(values["standard_work_days_per_year"]) <= Decimal("366"):
        raise ValueError("standard_work_days_per_year must be between 1 and 366")

    threshold_fields = (
        "attention_utilization_percent",
        "minimum_utilization_percent",
        "excellent_utilization_percent",
        "overload_utilization_percent",
    )
    thresholds = {field: Decimal(values[field]) for field in threshold_fields}
    for field, value in thresholds.items():
        if value <= 0 or value > 300:
            raise ValueError(f"{field} must be greater than 0 and no more than 300")
    if not (
        thresholds["attention_utilization_percent"]
        < thresholds["minimum_utilization_percent"]
        < thresholds["excellent_utilization_percent"]
        < thresholds["overload_utilization_percent"]
    ):
        raise ValueError(
            "utilization thresholds must satisfy attention < minimum < excellent < overload"
        )
    for name, (low, high, label, integer) in POLICY_RANGES.items():
        value = Decimal(values[name])
        if integer and value != value.to_integral_value():
            raise ValueError(f"{name} must be a whole number")
        if not low <= value <= high:
            raise ValueError(f"{name} ({label}) must be between {low} and {high}")
    budget_attention = Decimal(values["budget_attention_percent"])
    budget_high_risk = Decimal(values["budget_high_risk_percent"])
    if not Decimal("0") < budget_attention < budget_high_risk < Decimal("100"):
        raise ValueError("budget thresholds must satisfy 0 < attention < high risk < 100")


def _optional_decimal_text(value: Any) -> str | None:
    return _decimal_text(value) if value not in (None, "") else None


def _format_decimal(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def _validate_month(value: str) -> None:
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError as exc:
        raise ValueError("month must use YYYY-MM format") from exc


def _month_range(start: str, end: str) -> list[str]:
    current = datetime.strptime(start, "%Y-%m")
    finish = datetime.strptime(end, "%Y-%m")
    values = []
    while current <= finish:
        values.append(current.strftime("%Y-%m"))
        year = current.year + (1 if current.month == 12 else 0)
        month = 1 if current.month == 12 else current.month + 1
        current = current.replace(year=year, month=month)
    return values


def _previous_month(value: str) -> str:
    return _shift_month(value, -1)


def _is_newer_source_version(incoming: str | None, existing: str | None) -> bool:
    """Whether an imported time entry is a later version of the stored one."""
    if not incoming:
        return False
    if not existing:
        return True
    try:
        return datetime.fromisoformat(incoming) > datetime.fromisoformat(existing)
    except ValueError:
        return incoming > existing


def _employee_key(row: dict[str, Any]) -> str:
    """Employees are identified by ID; the name is only a fallback for rows without one."""
    return str(row.get("employee_id") or "").strip() or str(row.get("employee_name") or "").strip()


def _shift_month(value: str, offset: int) -> str:
    current = datetime.strptime(value, "%Y-%m")
    month_index = current.year * 12 + current.month - 1 + offset
    year, month_zero_based = divmod(month_index, 12)
    return f"{year:04d}-{month_zero_based + 1:02d}"


def _plan_months(planned_start: str | None, planned_end: str | None) -> int | None:
    if not planned_start or not planned_end:
        return None
    start = date.fromisoformat(planned_start)
    end = date.fromisoformat(planned_end)
    return max((end.year - start.year) * 12 + end.month - start.month + 1, 1)


def _format_percent(value: Decimal) -> str:
    return f"{value.normalize():f}%"


def _employee_period(
    period: str, month: str | None, year: str | None, now: str
) -> tuple[str, str, str]:
    if period not in {"month", "year"}:
        raise ValueError("period must be month or year")
    if period == "month":
        value = month or now[:7]
        _validate_month(value)
        start = datetime.strptime(value, "%Y-%m").date()
        next_month_year = start.year + (1 if start.month == 12 else 0)
        next_month = 1 if start.month == 12 else start.month + 1
        end = date(next_month_year, next_month, 1)
        end = date.fromordinal(end.toordinal() - 1)
        return start.isoformat(), end.isoformat(), value
    value = year or now[:4]
    if len(value) != 4 or not value.isdigit():
        raise ValueError("year must use YYYY format")
    return f"{value}-01-01", f"{value}-12-31", value


def _employee_worktime_rows(
    rows: list[dict[str, Any]],
    baseline: dict[str, Any],
    baseline_hours: Decimal,
    standard_hours_per_day: Decimal,
    *,
    known_employees: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = grouped.setdefault(
            _employee_key(row),
            {
                "employee_id": row["employee_id"],
                "employee_name": row["employee_name"],
                "reported_hours": Decimal("0"),
                "overtime_hours": Decimal("0"),
                "approved_hours": Decimal("0"),
                "entry_count": 0,
                "project_numbers": set(),
            },
        )
        overtime = _overtime_entry_hours(row)
        item["reported_hours"] += Decimal(row["hours"]) + overtime
        item["overtime_hours"] += overtime
        if row["approval_status"] == "approved":
            item["approved_hours"] += Decimal(row["approved_hours"])
        item["entry_count"] += 1
        item["project_numbers"].add(row["project_number"])

    for employee in known_employees or []:
        grouped.setdefault(
            _employee_key(employee),
            {
                "employee_id": employee["employee_id"],
                "employee_name": employee["employee_name"],
                "reported_hours": Decimal("0"),
                "overtime_hours": Decimal("0"),
                "approved_hours": Decimal("0"),
                "entry_count": 0,
                "project_numbers": set(),
            },
        )

    result = []
    for item in grouped.values():
        gap = max(item["reported_hours"] - item["approved_hours"], Decimal("0"))
        utilization = (
            item["reported_hours"] / baseline_hours * Decimal("100")
            if baseline_hours > 0 else Decimal("0")
        )
        result.append(
            {
                "employee_id": item["employee_id"],
                "employee_name": item["employee_name"],
                "reported_hours": _format_decimal(item["reported_hours"]),
                "overtime_hours": _format_decimal(item["overtime_hours"]),
                "approved_hours": _format_decimal(item["approved_hours"]),
                "confirmation_gap_hours": _format_decimal(gap),
                "reported_person_days": _format_decimal(
                    item["reported_hours"] / standard_hours_per_day
                    if standard_hours_per_day else Decimal("0")
                ),
                "overtime_person_days": _format_decimal(
                    item["overtime_hours"] / standard_hours_per_day
                    if standard_hours_per_day else Decimal("0")
                ),
                "approved_person_days": _format_decimal(
                    item["approved_hours"] / standard_hours_per_day
                    if standard_hours_per_day else Decimal("0")
                ),
                "baseline_hours": _format_decimal(baseline_hours),
                "utilization_percent": f"{utilization.quantize(Decimal('0.1'))}",
                "entry_count": item["entry_count"],
                "project_count": len(item["project_numbers"]),
                "status": _employee_utilization_status(utilization, baseline),
            }
        )
    return sorted(result, key=lambda row: (-float(row["utilization_percent"]), row["employee_name"]))


def _monitoring_time_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the effort that project and resource metrics count.

    TSG999 remains persisted as an auditable raw time-entry source, but is not a
    customer project and must not make a person appear allocated to a project.
    Rejected and draft entries stay stored but are not effort.
    """
    return [
        row for row in _effort_entries(rows)
        if row.get("project_number") != INTERNAL_NONPROJECT_CODE
    ]


def _employee_utilization_status(utilization: Decimal, baseline: dict[str, Any]) -> str:
    excellent = Decimal(baseline["excellent_utilization_percent"])
    minimum = Decimal(baseline["minimum_utilization_percent"])
    attention = Decimal(baseline["attention_utilization_percent"])
    if utilization > excellent:
        return "工时充足"
    if utilization > minimum:
        return "基本达标"
    if utilization > attention:
        return "需要关注"
    return "严重不足"
