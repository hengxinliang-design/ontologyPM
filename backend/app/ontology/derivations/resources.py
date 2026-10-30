"""Resource metrics, implemented once (ontology 7.8.0, requirements baseline G4, D13–D15).

Employee utilization (project hours ÷ capacity), capacity (working days × standard hours per day), the worktime
assessment, the staffing recommendation of resource operations and resource health all live here. The resource
operations page, the employee worktime page, the Employee object, the resource-health function and the department
review call this module, so one employee in one month has one utilization everywhere.

Internal non-project codes come from the investment settings (D15). Over the overload line in a single month is
「本月负荷过高」 (staffing); over it for N consecutive months is 「持续过载」 (health, D13). A first month is marked
as such instead of healthy (D14).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from ...project_repository import (
    _counts_as_effort,
    _cursor_page,
    _cursor_scope,
    _employee_key,
    _employee_period,
    _format_decimal,
    _overtime_entry_hours,
    _total_entry_hours,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")

# Staffing recommendation, most urgent first. 「本月负荷过高」 is the single-month reading of the overload line (D13).
ALLOCATION_ORDER = ("本月负荷过高", "负荷较高", "可优先调剂", "可适度支持", "安排基本均衡")
HIGH_LOAD = frozenset({"本月负荷过高", "负荷较高"})
# Resource health, most urgent first; first_month replaces healthy for a first month without other findings (D14).
HEALTH_ORDER = ("overloaded", "fatigue", "fragmented", "idle", "first_month", "healthy")


def _month_end(month: str) -> str:
    year, number = int(month[:4]), int(month[5:7])
    following = date(year + (number == 12), number % 12 + 1, 1)
    return date.fromordinal(following.toordinal() - 1).isoformat()


def _shift(month: str, offset: int) -> str:
    index = int(month[:4]) * 12 + int(month[5:7]) - 1 + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def is_internal(row: dict[str, Any], internal_codes: frozenset[str]) -> bool:
    return str(row.get("project_number") or "").upper() in internal_codes


def project_effort(rows: list[dict[str, Any]], internal_codes: frozenset[str]) -> list[dict[str, Any]]:
    """Effort that counts for utilization: submitted or approved, and not an internal non-project code."""
    return [row for row in rows if _counts_as_effort(row) and not is_internal(row, internal_codes)]


def utilization_percent(hours: Decimal, capacity_hours: Decimal) -> Decimal:
    return hours / capacity_hours * HUNDRED if capacity_hours > 0 else ZERO


def worktime_status(utilization: Decimal, policy: dict[str, Any]) -> str:
    if utilization > Decimal(policy["excellent_utilization_percent"]):
        return "工时充足"
    if utilization > Decimal(policy["minimum_utilization_percent"]):
        return "基本达标"
    if utilization > Decimal(policy["attention_utilization_percent"]):
        return "需要关注"
    return "严重不足"


def allocation(utilization: Decimal, reported_hours: Decimal, capacity_hours: Decimal, internal_hours: Decimal,
               policy: dict[str, Any]) -> tuple[str, Decimal]:
    """Staffing status and the hours the person can still take on in the period."""
    minimum = Decimal(policy["minimum_utilization_percent"])
    to_minimum = max(capacity_hours * minimum / HUNDRED - reported_hours, ZERO)
    if utilization <= Decimal(policy["attention_utilization_percent"]):
        status, available = "可优先调剂", to_minimum
    elif utilization <= minimum:
        status, available = "可适度支持", to_minimum
    elif utilization > Decimal(policy["overload_utilization_percent"]):
        status, available = "本月负荷过高", ZERO
    elif utilization > Decimal(policy["excellent_utilization_percent"]):
        status, available = "负荷较高", ZERO
    else:
        status, available = "安排基本均衡", ZERO
    # Internal non-project hours can be moved to projects: take the larger of them and the capacity gap, without
    # counting twice. People already at high load are not offered, even with internal hours.
    if status not in HIGH_LOAD:
        available = max(available, internal_hours)
    return status, available


def worktime_rows(rows: list[dict[str, Any]], internal_codes: frozenset[str], policy: dict[str, Any],
                  capacity_hours: Decimal) -> list[dict[str, Any]]:
    """Worktime assessment per employee. Everyone with any entry in the period is listed (0% when none counts)."""
    hours_per_day = Decimal(policy["standard_hours_per_day"])
    grouped: dict[str, dict[str, Any]] = {}

    def person(row: dict[str, Any]) -> dict[str, Any]:
        return grouped.setdefault(_employee_key(row), {
            "employee_id": row["employee_id"], "employee_name": row["employee_name"], "reported_hours": ZERO,
            "overtime_hours": ZERO, "approved_hours": ZERO, "entry_count": 0, "project_numbers": set()})

    for row in project_effort(rows, internal_codes):
        item = person(row)
        overtime = _overtime_entry_hours(row)
        item["reported_hours"] += Decimal(row["hours"]) + overtime
        item["overtime_hours"] += overtime
        if row["approval_status"] == "approved":
            item["approved_hours"] += Decimal(row["approved_hours"])
        item["entry_count"] += 1
        item["project_numbers"].add(row["project_number"])
    for row in rows:
        person(row)

    def days(hours: Decimal) -> str:
        return _format_decimal(hours / hours_per_day if hours_per_day else ZERO)

    result = []
    for item in grouped.values():
        utilization = utilization_percent(item["reported_hours"], capacity_hours)
        result.append({
            "employee_id": item["employee_id"],
            "employee_name": item["employee_name"],
            "reported_hours": _format_decimal(item["reported_hours"]),
            "overtime_hours": _format_decimal(item["overtime_hours"]),
            "approved_hours": _format_decimal(item["approved_hours"]),
            "confirmation_gap_hours": _format_decimal(max(item["reported_hours"] - item["approved_hours"], ZERO)),
            "reported_person_days": days(item["reported_hours"]),
            "overtime_person_days": days(item["overtime_hours"]),
            "approved_person_days": days(item["approved_hours"]),
            "baseline_hours": _format_decimal(capacity_hours),
            "utilization_percent": f"{utilization.quantize(Decimal('0.1'))}",
            "entry_count": item["entry_count"],
            "project_count": len(item["project_numbers"]),
            "status": worktime_status(utilization, policy),
        })
    return sorted(result, key=lambda row: (-float(row["utilization_percent"]), row["employee_name"]))


def monthly_buckets(rows: list[dict[str, Any]], internal_codes: frozenset[str]) -> dict[str, dict[str, Any]]:
    """Per employee and month: project, internal and overtime hours and the projects worked on (effort only)."""
    people: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _counts_as_effort(row):
            continue
        person = people.setdefault(str(row["employee_id"] or row["employee_name"]),
                                   {"employee_id": row["employee_id"], "employee_name": row["employee_name"], "months": {}})
        bucket = person["months"].setdefault(row["work_date"][:7], {
            "project_hours": ZERO, "internal_hours": ZERO, "overtime_hours": ZERO, "projects": set()})
        overtime = _overtime_entry_hours(row)
        bucket["overtime_hours"] += overtime
        if is_internal(row, internal_codes):
            bucket["internal_hours"] += _total_entry_hours(row)
        else:
            bucket["project_hours"] += _total_entry_hours(row)
            bucket["projects"].add(row["project_number"])
    return people


def resource_health(months: dict[str, dict[str, Any]], month: str, capacity_hours: dict[str, Decimal],
                    policy: dict[str, Any]) -> dict[str, Any]:
    """R12: one employee's health in ``month`` from monthly aggregates (project, internal, overtime hours, projects)."""
    def utilization(m: str) -> Decimal | None:
        cap = capacity_hours.get(m)
        return months[m]["project_hours"] / cap * HUNDRED if m in months and cap else None

    current = months.get(month) or {"project_hours": ZERO, "internal_hours": ZERO, "overtime_hours": ZERO, "projects": set()}
    util = utilization(month) or ZERO
    overload_line = Decimal(policy["overload_utilization_percent"])
    # Consecutive calendar months ending at ``month``: a month without hours breaks the streak.
    streak, cursor = 0, month
    while (value := utilization(cursor)) is not None and value > overload_line:
        streak += 1
        cursor = _shift(cursor, -1)
    total_hours = current["project_hours"] + current["internal_hours"]
    overtime_share = current["overtime_hours"] / total_hours * HUNDRED if total_hours else ZERO
    concurrent = len(current["projects"])
    first_month = month == min(months) if months else True
    reasons, statuses = [], []
    if streak >= int(Decimal(policy["overload_consecutive_months"])):
        statuses.append("overloaded"); reasons.append(f"连续 {streak} 个月利用率高于过载线 {overload_line}%")
    if overtime_share > Decimal(policy["overtime_share_attention_percent"]):
        statuses.append("fatigue"); reasons.append(f"加班占比 {overtime_share.quantize(Decimal('0.1'))}% 高于关注线 {policy['overtime_share_attention_percent']}%")
    if concurrent > int(Decimal(policy["max_concurrent_projects"])):
        statuses.append("fragmented"); reasons.append(f"同期参与 {concurrent} 个项目，超过上限 {int(Decimal(policy['max_concurrent_projects']))} 个")
    if util <= Decimal(policy["attention_utilization_percent"]) and not first_month and month in months:
        statuses.append("idle"); reasons.append(f"利用率 {util.quantize(Decimal('0.1'))}% 不高于关注线 {policy['attention_utilization_percent']}%")
    if not statuses and first_month and month in months:
        # D14: a first month is not judged idle, but it is not called healthy either.
        statuses.append("first_month"); reasons.append("统计窗口内第一个有工时的月份，不评价闲置")
    return {
        "month": month,
        "status": statuses[0] if statuses else "healthy",
        "statuses": statuses or ["healthy"],
        "reasons": reasons,
        "utilization_percent": float(util.quantize(Decimal("0.1"))),
        "consecutive_overload_months": streak,
        "overtime_share_percent": float(overtime_share.quantize(Decimal("0.1"))),
        "concurrent_project_count": concurrent,
        "first_month": first_month,
    }


class ResourceMetrics:
    """Loads time entries, the operating policy, the calendar and the internal codes, then applies the rules above."""

    def __init__(self, projects: Any):
        self.projects = projects
        self.database = projects.database

    def _now(self) -> str:
        return self.projects._now()

    def internal_codes(self) -> frozenset[str]:
        return frozenset(code.upper() for code in self.projects.internal_codes())

    def entries(self, start_date: str, end_date: str, employee_name: str = "") -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM time_entries WHERE work_date >= ? AND work_date <= ? AND (? = '' OR employee_name LIKE ?) "
                "ORDER BY employee_name, work_date DESC",
                (start_date, end_date, employee_name, f"%{employee_name}%"),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_work_month(self) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT MAX(work_date) AS latest FROM time_entries").fetchone()
        return row["latest"][:7] if row and row["latest"] else None

    def capacity(self, start_date: str, end_date: str, policy: dict[str, Any]) -> tuple[Decimal, dict[str, Any]]:
        calendar = self.projects.workday_summary(start_date, end_date)
        return Decimal(calendar["working_days"]) * Decimal(policy["standard_hours_per_day"]), calendar

    def monthly_capacity(self, months: list[str], policy: dict[str, Any]) -> dict[str, Decimal]:
        return {m: self.capacity(f"{m}-01", _month_end(m), policy)[0] for m in months}

    def monthly_hours(self, months: list[str]) -> dict[str, dict[str, Any]]:
        return monthly_buckets(self.entries(f"{months[0]}-01", _month_end(months[-1])), self.internal_codes())

    @staticmethod
    def _calendar_meta(capacity_hours: Decimal, calendar: dict[str, Any]) -> dict[str, Any]:
        return {
            "baseline_hours": _format_decimal(capacity_hours),
            "working_days": calendar["working_days"],
            "holiday_excluded_days": calendar["holiday_excluded_days"],
            "makeup_workdays": calendar["makeup_workdays"],
            "calendar_available": calendar["available"],
            "calendar_stale": calendar["stale"],
        }

    # ------------------------------------------------------------ employee worktime assessment
    def worktime_assessment(self, *, period: str = "month", month: str | None = None, year: str | None = None,
                            employee_name: str | None = None) -> dict[str, Any]:
        policy = self.projects.get_worktime_baseline()
        start_date, end_date, label = _employee_period(period, month, year, self._now())
        rows = self.entries(start_date, end_date)
        if employee_name:
            rows = [row for row in rows if row["employee_name"] == employee_name]
        internal = self.internal_codes()
        capacity_hours, calendar = self.capacity(start_date, end_date, policy)
        grouped = worktime_rows(rows, internal, policy, capacity_hours)
        return {
            "data": grouped,
            "summary": {
                "total_employees": len(grouped),
                "sufficient_count": sum(row["status"] == "工时充足" for row in grouped),
                "qualified_count": sum(row["status"] == "基本达标" for row in grouped),
                "attention_count": sum(row["status"] == "需要关注" for row in grouped),
                "critical_count": sum(row["status"] == "严重不足" for row in grouped),
            },
            "baseline": policy,
            "meta": {
                "period": period, "period_label": label, "start_date": start_date, "end_date": end_date,
                **self._calendar_meta(capacity_hours, calendar),
                "latest_work_month": self.latest_work_month(),
                "excluded_internal_nonproject_entries": sum(is_internal(row, internal) for row in rows),
            },
        }

    # ------------------------------------------------------------ resource operations
    def resource_operations(self, *, month: str | None = None, start_month: str | None = None,
                            end_month: str | None = None, employee_name: str | None = None,
                            cursor: str | None = None, page_size: int = 50,
                            project_names: dict[str, str]) -> dict[str, Any]:
        """PMO capacity and staffing view from imported worktime, the worktime baseline and project plans only.

        Skills, departments and availability outside the source system are not assumed or fabricated.
        """
        policy = self.projects.get_worktime_baseline()
        latest_month = self.latest_work_month()
        if (start_month is None) != (end_month is None):
            raise ValueError("start_month and end_month must be provided together")
        if month and start_month:
            raise ValueError("month cannot be combined with a date range")
        if start_month and end_month:
            start_date, _, normalized_start_month = _employee_period("month", start_month, None, self._now())
            _, end_date, normalized_end_month = _employee_period("month", end_month, None, self._now())
            if start_date > end_date:
                raise ValueError("start_month must not be after end_month")
            effective_month = normalized_end_month
            period_label = f"{normalized_start_month} 至 {normalized_end_month}"
        else:
            effective_month = month or latest_month or self._now()[:7]
            start_date, end_date, _ = _employee_period("month", effective_month, None, self._now())
            normalized_start_month = normalized_end_month = effective_month
            period_label = effective_month
        normalized_employee_name = (employee_name or "").strip()
        rows = self.entries(start_date, end_date, normalized_employee_name)
        internal = self.internal_codes()
        hours_per_day = Decimal(policy["standard_hours_per_day"])
        capacity_hours, calendar = self.capacity(start_date, end_date, policy)
        assessment_rows = worktime_rows(rows, internal, policy, capacity_hours)

        def days(hours: Decimal) -> str:
            return _format_decimal(hours / hours_per_day if hours_per_day else ZERO)

        employee_projects: dict[str, dict[str, dict[str, Decimal]]] = {}
        for row in project_effort(rows, internal):
            project = employee_projects.setdefault(_employee_key(row), {}).setdefault(
                row["project_number"], {"reported_hours": ZERO, "overtime_hours": ZERO})
            project["reported_hours"] += _total_entry_hours(row)
            project["overtime_hours"] += _overtime_entry_hours(row)
        internal_time: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not (_counts_as_effort(row) and is_internal(row, internal)):
                continue
            item = internal_time.setdefault(_employee_key(row), {"reported_hours": ZERO, "overtime_hours": ZERO, "entry_count": 0})
            item["reported_hours"] += _total_entry_hours(row)
            item["overtime_hours"] += _overtime_entry_hours(row)
            item["entry_count"] += 1

        employees: list[dict[str, Any]] = []
        total_available_days = ZERO
        available_count = high_load_count = overloaded_count = 0
        empty = {"reported_hours": ZERO, "overtime_hours": ZERO, "entry_count": 0}
        for assessment in assessment_rows:
            key = _employee_key(assessment)
            own_internal = internal_time.get(key, empty)
            status, available_hours = allocation(
                Decimal(assessment["utilization_percent"]), Decimal(assessment["reported_hours"]), capacity_hours,
                own_internal["reported_hours"], policy)
            high_load_count += status in HIGH_LOAD
            overloaded_count += status == "本月负荷过高"
            available_count += available_hours > 0
            assignments = [
                {"project_number": number, "project_name": project_names.get(number, number),
                 "reported_person_days": days(values["reported_hours"]), "overtime_person_days": days(values["overtime_hours"])}
                for number, values in employee_projects.get(key, {}).items()
            ]
            if own_internal["reported_hours"] > 0:
                assignments.append({
                    "project_number": "、".join(sorted(internal)), "project_name": "内部非项目工时（可调剂）",
                    "reported_person_days": days(own_internal["reported_hours"]),
                    "overtime_person_days": days(own_internal["overtime_hours"]), "is_internal_nonproject": True})
            assignments.sort(key=lambda item: (not item.get("is_internal_nonproject", False),
                                               -Decimal(item["reported_person_days"]), item["project_number"]))
            available_days = available_hours / hours_per_day if hours_per_day else ZERO
            total_available_days += available_days
            employees.append({
                **assessment,
                "allocation_status": status,
                "available_person_days": _format_decimal(available_days),
                "internal_nonproject_person_days": days(own_internal["reported_hours"]),
                "internal_nonproject_entry_count": own_internal["entry_count"],
                "assignments": assignments,
            })
        order = {status: index for index, status in enumerate(ALLOCATION_ORDER)}
        employees.sort(key=lambda item: (order[item["allocation_status"]], item["employee_name"]))
        page, next_cursor = _cursor_page(
            employees, cursor=cursor, page_size=page_size, kind="resource-operations",
            key=lambda item: (order[item["allocation_status"]], item["employee_name"], item["employee_id"]),
            scope=_cursor_scope(month=month, start_month=start_month, end_month=end_month, employee_name=normalized_employee_name),
        )
        codes = "、".join(sorted(internal)) or "无"
        return {
            "data": page,
            "summary": {
                "total_employees": len(employees),
                "available_count": available_count,
                "high_load_count": high_load_count,
                "overloaded_count": overloaded_count,
                "available_person_days": _format_decimal(total_available_days),
            },
            "baseline": policy,
            "meta": {
                "month": effective_month, "period_label": period_label,
                "start_month": normalized_start_month, "end_month": normalized_end_month,
                "employee_name": normalized_employee_name or None,
                "start_date": start_date, "end_date": end_date,
                **self._calendar_meta(capacity_hours, calendar),
                "latest_work_month": latest_month,
                "excluded_internal_nonproject_entries": sum(is_internal(row, internal) for row in rows),
                "internal_nonproject_project_code": codes,
                "recommendation_limitations": f"内部非项目工时（{codes}，在投入设置中维护）不计入资源监控；其余建议仅基于导入工时、工时基线与项目计划，未包含员工技能、角色、请假或外部排期。",
                "page_size": page_size,
                "next_cursor": next_cursor,
            },
        }

    # ------------------------------------------------------------ resource health
    def resource_health(self, month: str | None = None) -> dict[str, Any]:
        policy = self.projects.get_worktime_baseline()
        month = month or self.latest_work_month() or self._now()[:7]
        window = [_shift(month, -offset) for offset in range(11, -1, -1)]
        capacity = self.monthly_capacity(window, policy)
        rows = []
        for person in self.monthly_hours(window).values():
            if month not in person["months"]:  # only people who reported hours in the month (R12)
                continue
            rows.append({"employee_id": person["employee_id"], "employee_name": person["employee_name"],
                         **resource_health(person["months"], month, capacity, policy)})
        rows.sort(key=lambda r: (HEALTH_ORDER.index(r["status"]), r["employee_name"]))
        summary: dict[str, int] = {}
        for row in rows:
            summary[row["status"]] = summary.get(row["status"], 0) + 1
        return {"month": month, "data": rows, "summary": summary}
