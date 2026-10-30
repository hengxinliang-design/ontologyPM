"""Department PMO economics in person-days, derived in the ontology (derivation ``projectEconomics`` and the
department functions; design: docs/ontology-design/2026-10-26-pmo-ontology-v3.md).

Pure functions implement the rule cards of docs/ontology-builds/2026-10-26-pmo-economics.yaml so they can be
checked against the finance example directly; ``EconomicsAnalysis`` only gathers data for them.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ...project_repository import _counts_as_effort
from .resources import ResourceMetrics

ZERO = Decimal("0")
HUNDRED = Decimal("100")
PAID_STATUSES = {"paid", "partially_paid"}
CLOSED_LIFECYCLES = {"completed", "closed"}
INTERNAL_CODE = "TSG999"

# R15: signals in priority order.
SIGNAL_ORDER = ("push_acceptance", "control_cost", "staffing_support", "win_contracts", "release_resources", "hold")
SIGNAL_LABELS = {
    "push_acceptance": "推动验收回款", "control_cost": "控制成本", "staffing_support": "招聘或外部支援",
    "win_contracts": "争取新合同", "release_resources": "释放资源", "hold": "保持",
}


def d(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        value = value.get("amount")
    return Decimal(str(value))


def money(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(Decimal("0.01"), ROUND_HALF_UP))


def pct(value: Decimal | None) -> float | None:
    return None if value is None else float((value * HUNDRED).quantize(Decimal("0.01"), ROUND_HALF_UP))


def recognition_method(contract_type: str, workload_settlement: bool) -> str:
    """R06 / design §5.1: project type decides how revenue is confirmed."""
    if contract_type == "open_ended" or workload_settlement:
        return "workload"
    if contract_type in {"construction", "maintenance"}:
        return "milestone"
    return "none"


def compute_project_economics(
    *,
    contract_value: Decimal,
    invoiced_amount: Decimal,
    crm_received_amount: Decimal,
    contract_type: str,
    workload_settlement: bool,
    lifecycle: str,
    used_person_days: Decimal,
    planned_person_days: Decimal | None,
    payments: list[dict[str, Any]],
    settings: dict[str, Any],
    policy: dict[str, Any],
    today: date,
) -> dict[str, Any]:
    """Rule cards R01–R10 for one project."""
    flags: list[str] = []
    reasons: list[str] = []
    tax = (d(settings.get("tax_rate_percent")) if settings.get("tax_rate_percent") not in (None, "")
           else d(policy["default_tax_rate_percent"])) / HUNDRED
    margin_overridden = settings.get("target_gross_margin_percent") not in (None, "")
    target = d(settings["target_gross_margin_percent"] if margin_overridden else policy["default_target_margin_percent"]) / HUNDRED
    procurement = d(settings.get("external_procurement_amount")) or ZERO
    commercial = d(settings.get("commercial_expense_amount")) or ZERO
    deductions = procurement + commercial
    rate = d(settings.get("contract_person_day_rate"))
    warning_points = d(policy["cost_ahead_warning_points"])

    has_contract = contract_value > 0
    estimated_revenue = contract_value / (1 + tax) - deductions if has_contract else None          # R01
    if estimated_revenue is not None and estimated_revenue < 0:
        flags.append("扣减项超过税后合同额，请核对外部采购与商务费用")
    available_cost = estimated_revenue * (1 - target) if estimated_revenue is not None else None   # R02
    disposable_days = available_cost / rate if available_cost is not None and rate else None        # R03
    dynamic_cost = used_person_days * rate if rate else None                                       # R04

    # R05: remaining effort — PM estimate, else budget minus used, zero once delivery has ended.
    estimate = d(settings.get("remaining_person_days_estimate"))
    if lifecycle in CLOSED_LIFECYCLES:
        remaining, remaining_source = ZERO, "项目已结束"
    elif estimate is not None:
        remaining, remaining_source = estimate, "项目经理估算"
    elif planned_person_days is not None:
        remaining, remaining_source = max(planned_person_days - used_person_days, ZERO), "预算人天 − 已用人天"
        flags.append("剩余人天估算缺失，按预算人天 − 已用人天估算")
    else:
        remaining, remaining_source = None, None
    estimated_margin = forecast_margin = None
    if estimated_revenue and estimated_revenue > 0 and rate:
        estimated_margin = (estimated_revenue - dynamic_cost) / estimated_revenue
        if remaining is not None:
            forecast_margin = (estimated_revenue - (used_person_days + remaining) * rate) / estimated_revenue

    # R06: confirmed revenue by recognition method.
    method = recognition_method(contract_type, workload_settlement)
    paid_amount = sum((d(p.get("actual_amount")) or ZERO for p in payments if p.get("status") in PAID_STATUSES), ZERO)
    if method == "milestone" and has_contract:
        confirmed_revenue = paid_amount / (1 + tax) - deductions * (paid_amount / contract_value)
    elif method == "workload":
        confirmed_revenue = invoiced_amount / (1 + tax)
    else:
        confirmed_revenue = None
    confirmed_margin = None                                                                      # R07
    if confirmed_revenue and confirmed_revenue > 0 and dynamic_cost is not None:
        confirmed_margin = (confirmed_revenue - dynamic_cost) / confirmed_revenue

    # R08: cost ahead of revenue, with a grace period before the first collection is due.
    cost_progress = dynamic_cost / available_cost if dynamic_cost is not None and available_cost and available_cost > 0 else None
    revenue_progress = (confirmed_revenue or ZERO) / estimated_revenue if estimated_revenue and estimated_revenue > 0 and method != "none" else None
    cost_ahead = (cost_progress - revenue_progress) * HUNDRED if cost_progress is not None and revenue_progress is not None else None
    first_due = min((p["planned_date"] for p in payments if p.get("planned_date")), default=None)
    in_grace = bool(first_due) and today.isoformat() < first_due

    # R10: milestone nodes are authoritative; CRM collections only reconcile.
    if method == "milestone" and has_contract and abs(paid_amount - crm_received_amount) > Decimal("0.01"):
        flags.append(f"回款对账差异 {money(abs(paid_amount - crm_received_amount))}：节点到账 {money(paid_amount)}，CRM 已回款 {money(crm_received_amount)}")
    if disposable_days is not None and planned_person_days is not None and planned_person_days > disposable_days:
        flags.append(f"预算人天 {money(planned_person_days)} 超过可支配人天 {money(disposable_days)}")

    # R09: status in order of severity.
    if not has_contract or not rate:
        status = "no_baseline"
        reasons.append("缺少合同额" if not has_contract else "缺少合同人天单价")
    else:
        exceeded = []
        if dynamic_cost > available_cost:
            exceeded.append(f"动态成本 {money(dynamic_cost)} 超过可用成本 {money(available_cost)}")
        if cost_ahead is not None and cost_ahead >= warning_points:
            if in_grace:
                flags.append(f"成本超前 {cost_ahead.quantize(Decimal('0.01'))} 个百分点，首个回款节点 {first_due} 前处于宽限期")
            else:
                exceeded.append(f"成本超前 {cost_ahead.quantize(Decimal('0.01'))} 个百分点，达到预警线 {warning_points}")
        warnings = []
        if forecast_margin is not None and forecast_margin < target:
            warnings.append(f"预计完工毛利率 {pct(forecast_margin)}% 低于目标 {pct(target)}%")
        if exceeded:
            status, reasons = "exceeded", exceeded + warnings
        elif warnings:
            status, reasons = "warning", warnings
        else:
            status = "on_target"

    return {
        "recognition_method": method,
        "tax_rate_percent": pct(tax),
        "target_gross_margin_percent": pct(target),
        "target_margin_overridden": margin_overridden,
        "target_margin_reason": settings.get("target_margin_reason"),
        "contract_person_day_rate": money(rate),
        "deductions": money(deductions),
        "estimated_revenue": money(estimated_revenue),
        "available_cost": money(available_cost),
        "disposable_person_days": money(disposable_days),
        "used_person_days": money(used_person_days),
        "remaining_person_days": money(remaining),
        "remaining_source": remaining_source,
        "remaining_disposable_person_days": money(disposable_days - used_person_days) if disposable_days is not None else None,
        "dynamic_cost": money(dynamic_cost),
        "estimated_gross_margin_percent": pct(estimated_margin),
        "forecast_gross_margin_percent": pct(forecast_margin),
        "milestone_paid_amount": money(paid_amount),
        "confirmed_revenue": money(confirmed_revenue),
        "confirmed_gross_margin_percent": pct(confirmed_margin),
        "cost_progress_percent": pct(cost_progress),
        "revenue_progress_percent": pct(revenue_progress),
        "cost_ahead_points": float(cost_ahead.quantize(Decimal("0.01"), ROUND_HALF_UP)) if cost_ahead is not None else None,
        "in_grace_period": in_grace,
        "status": status,
        "reasons": reasons,
        "data_flags": flags,
    }


def rollup(items: list[dict[str, Any]]) -> dict[str, Any]:
    """R11: aggregate project economics; the aggregate never hides an exceeded project."""
    base = [i for i in items if i["status"] != "no_baseline"]
    revenue = sum((d(i["estimated_revenue"]) for i in base), ZERO)
    cost = sum((d(i["dynamic_cost"]) for i in base), ZERO)
    available = sum((d(i["available_cost"]) for i in base), ZERO)
    confirmed = sum((d(i["confirmed_revenue"]) or ZERO for i in base), ZERO)
    return {
        "project_count": len(items),
        "baseline_project_count": len(base),
        "estimated_revenue": money(revenue),
        "available_cost": money(available),
        "dynamic_cost": money(cost),
        "confirmed_revenue": money(confirmed),
        "estimated_gross_margin_percent": pct((revenue - cost) / revenue) if revenue > 0 else None,
        "confirmed_gross_margin_percent": pct((confirmed - cost) / confirmed) if confirmed > 0 else None,
        "exceeded_projects": [i["project_number"] for i in items if i["status"] == "exceeded"],
        "warning_projects": [i["project_number"] for i in items if i["status"] == "warning"],
        "no_baseline_projects": [i["project_number"] for i in items if i["status"] == "no_baseline"],
    }


def plan_completion(actual: Decimal | None, target: Decimal | None, month: int, tolerance_points: Decimal, cumulative: bool = True) -> dict[str, Any]:
    """R14: year-to-date completion against time progress (cumulative KPIs) or against the target (rates)."""
    time_progress = Decimal(month) / Decimal(12) * HUNDRED
    if actual is None:
        return {"actual": None, "target": money(target), "completion_percent": None,
                "time_progress_percent": float(time_progress.quantize(Decimal("0.01"))), "status": "no_data"}
    if target is None or target <= 0:
        return {"actual": money(actual), "target": None, "completion_percent": None,
                "time_progress_percent": float(time_progress.quantize(Decimal("0.01"))), "status": "no_target"}
    if cumulative:
        completion = actual / target * HUNDRED
        lag = time_progress - completion
        status = "behind" if lag > tolerance_points else "on_track"
    else:
        completion, lag = actual, target - actual
        status = "behind" if actual < target else "on_track"
    return {"actual": money(actual), "target": money(target),
            "completion_percent": float(completion.quantize(Decimal("0.01"), ROUND_HALF_UP)),
            "time_progress_percent": float(time_progress.quantize(Decimal("0.01"), ROUND_HALF_UP)),
            "lag_points": float(lag.quantize(Decimal("0.01"), ROUND_HALF_UP)), "status": status}


def backlog_coverage(remaining_disposable_days: Decimal, next_year_capacity_days: Decimal) -> Decimal | None:
    """R16: share of next year's capacity already covered by contracts in hand."""
    return remaining_disposable_days / next_year_capacity_days * HUNDRED if next_year_capacity_days > 0 else None


def strategy_signals(facts: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    """R15/R16: advisory signals with their evidence, in priority order."""
    found: dict[str, list[str]] = {}
    if facts["acceptance_projects"]:
        found["push_acceptance"] = [f"{p}：{why}" for p, why in facts["acceptance_projects"]]
    exceeded = facts["exceeded_projects"]
    margin, target = facts.get("forecast_margin_percent"), float(policy["default_target_margin_percent"])
    cost_evidence = []
    if len(exceeded) >= int(d(policy["control_cost_exceeded_projects"])):
        cost_evidence.append(f"{len(exceeded)} 个项目超标：{'、'.join(exceeded)}")
    if margin is not None and margin < target:
        cost_evidence.append(f"部门预计完工毛利率 {margin}% 低于目标 {target}%")
    if cost_evidence:
        found["control_cost"] = cost_evidence
    excellent = float(policy["excellent_utilization_percent"])
    recent = facts.get("recent_utilization", [])
    if facts["demand_next3_days"] > facts["capacity_next3_days"] > 0 and len(recent) >= 2 and all(u > excellent for u in recent[-2:]):
        found["staffing_support"] = [f"未来三个月需求 {facts['demand_next3_days']} 人天，超过产能 {facts['capacity_next3_days']} 人天",
                                     f"最近两个月利用率 {recent[-2]}%、{recent[-1]}% 高于优秀线 {excellent}%"]
    coverage, coverage_line = facts.get("backlog_coverage_percent"), float(policy["backlog_coverage_percent"])
    win = []
    if coverage is not None and coverage < coverage_line:
        win.append(f"在手合同覆盖明年产能 {coverage}%，低于覆盖线 {coverage_line}%")
    if recent and recent[-1] <= float(policy["attention_utilization_percent"]):
        win.append(f"最近一个月利用率 {recent[-1]}% 不高于关注线 {policy['attention_utilization_percent']}%")
    if win:
        found["win_contracts"] = win
    if facts["release_projects"]:
        found["release_resources"] = [f"{p}：{why}" for p, why in facts["release_projects"]]
    if not found:
        found["hold"] = ["没有触发任何调整信号"]
    return [{"signal": key, "label": SIGNAL_LABELS[key], "priority": SIGNAL_ORDER.index(key) + 1, "evidence": found[key]}
            for key in SIGNAL_ORDER if key in found]


def _month_end(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    nxt = date(year + (mon == 12), 1 if mon == 12 else mon + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1).isoformat()


def _shift(month: str, offset: int) -> str:
    index = int(month[:4]) * 12 + int(month[5:7]) - 1 + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


class DepartmentAnalysis:
    """Department analytics over the project facts derived in the ontology.

    ``project_facts`` returns every project with its derived facts; time entries, the working calendar and
    annual plans are read from the base programs.  Person-day economics is paused (ontology 4.0.0), so the
    review leaves out confirmed revenue, margins, backlog coverage and the signals that need them.
    """

    def __init__(self, database: Any, projects: Any, records: Any, plans: Any, timezone: str,
                 project_facts: Callable[[], list[dict[str, Any]]]):
        self.database = database
        self.projects = projects
        self.records = records
        self.plans = plans
        self.timezone = timezone
        self.project_facts = project_facts

    def _today(self) -> date:
        return datetime.now(ZoneInfo(self.timezone)).date()

    def _now(self) -> str:
        return datetime.now(ZoneInfo(self.timezone)).isoformat(timespec="seconds")

    # -------------------------------------------------------------- resources
    # Utilization, capacity and resource health have one implementation: derivations/resources.py (G4).
    @property
    def resources(self) -> ResourceMetrics:
        return ResourceMetrics(self.projects)

    def _monthly_hours(self, months: list[str]) -> dict[str, dict[str, Any]]:
        return self.resources.monthly_hours(months)

    def _capacity(self, months: list[str], hours_per_day: Decimal) -> dict[str, Decimal]:
        return self.resources.monthly_capacity(months, {"standard_hours_per_day": hours_per_day})

    def latest_work_month(self) -> str:
        return self.resources.latest_work_month() or self._today().isoformat()[:7]

    def resource_health(self, month: str | None = None) -> dict[str, Any]:
        return self.resources.resource_health(month or self.latest_work_month())

    # -------------------------------------------------------------- department review
    def department_review(self, year: int | None = None, month: str | None = None) -> dict[str, Any]:
        policy = self.projects.get_worktime_baseline()
        today = self._today()
        month = month or min(self.latest_work_month(), today.isoformat()[:7])
        year = year or int(month[:4])
        if int(month[:4]) != year:
            month = f"{year}-12" if int(month[:4]) > year else f"{year}-01"
        month_no = int(month[5:7])
        hours_per_day = Decimal(policy["standard_hours_per_day"])
        projects = self.project_facts()

        # Year-to-date actuals.
        period_end = _month_end(month)
        new_contracts = ZERO
        snapshot = self.records.export_records() or {"records": []}
        for record in snapshot["records"]:
            signed = record.get("signed_at") or ""
            if record.get("data_type") == "contract_order" and signed[:4] == str(year) and signed <= period_end:
                new_contracts += d(record.get("total_amount")) or ZERO
        collections_ytd = ZERO
        for project in projects:
            for payment in project.get("payment_nodes", []):
                actual_date = payment.get("actual_date") or ""
                if payment.get("status") in PAID_STATUSES and actual_date[:4] == str(year) and actual_date <= period_end:
                    collections_ytd += d(payment.get("actual_amount")) or ZERO

        # Capacity and utilisation by month.
        months = [f"{year}-{m:02d}" for m in range(1, month_no + 1)]
        people = self._monthly_hours(months)
        capacity = self._capacity(months, hours_per_day)
        utilization_by_month: list[dict[str, Any]] = []
        used_days_ytd = ZERO
        for m in months:
            active = [p for p in people.values() if m in p["months"]]
            project_hours = sum((p["months"][m]["project_hours"] for p in active), ZERO)
            used_days_ytd += project_hours / hours_per_day
            denominator = capacity[m] * len(active)
            if denominator:
                utilization_by_month.append({"month": m, "utilization_percent": float((project_hours / denominator * HUNDRED).quantize(Decimal("0.1"))),
                                             "active_employees": len(active)})
        headcount = len([p for p in people.values() if month in p["months"]])
        ytd_capacity_hours = sum((capacity[m] * len([p for p in people.values() if m in p["months"]]) for m in months), ZERO)
        ytd_utilization = float((used_days_ytd * hours_per_day / ytd_capacity_hours * HUNDRED).quantize(Decimal("0.1"))) if ytd_capacity_hours else None

        plan = self.plans.get_plan(year)
        tolerance = Decimal(policy["plan_lag_tolerance_points"])
        target = lambda f: d(plan.get(f)) if plan else None
        kpis = {
            "new_contracts": {"label": "新签合同额", **plan_completion(new_contracts, target("new_contract_target"), month_no, tolerance)},
            "collections": {"label": "回款", **plan_completion(collections_ytd, target("collection_target"), month_no, tolerance)},
            "capacity_used": {"label": "项目投入人天", **plan_completion(used_days_ytd, target("capacity_person_days_target"), month_no, tolerance)},
            "utilization": {"label": "产能利用率", **plan_completion(Decimal(str(ytd_utilization)) if ytd_utilization is not None else None,
                                                                 target("utilization_target_percent"), month_no, tolerance, cumulative=False)},
            "headcount": {"label": "在岗人数", **plan_completion(Decimal(headcount), target("headcount_target"), month_no, tolerance, cumulative=False)},
        }

        # Signals that do not need person-day economics (R15): acceptance, low utilisation, planned releases.
        today_iso = today.isoformat()
        acceptance = []
        for project in projects:
            payments = project.get("payment_nodes", [])
            unpaid = [p for p in payments if p.get("status") not in PAID_STATUSES]
            overdue = [p for p in unpaid if p.get("planned_date") and p["planned_date"] < today_iso]
            if overdue:
                acceptance.append((project["project_number"], f"{len(overdue)} 个回款节点逾期未到账"))
            elif project["lifecycle_status"] in CLOSED_LIFECYCLES and unpaid:
                acceptance.append((project["project_number"], f"交付已结束，仍有 {len(unpaid)} 个回款节点未到账"))
        recent_projects = {code for p in people.values() for code in p["months"].get(month, {}).get("projects", set())}
        release = [
            (project["project_number"], f"计划于 {project['planned_end']} 结束，人员可陆续释放")
            for project in projects
            if project.get("master_category") == "项目" and project["lifecycle_status"] not in CLOSED_LIFECYCLES
            and project.get("planned_end") and project["planned_end"][:7] <= _shift(month, 1)
            and project["project_number"] in recent_projects
        ]
        facts = {
            "acceptance_projects": acceptance,
            "exceeded_projects": [],
            "forecast_margin_percent": None,
            "demand_next3_days": 0.0,
            "capacity_next3_days": 0.0,
            "recent_utilization": [u["utilization_percent"] for u in utilization_by_month[-2:]],
            "backlog_coverage_percent": None,
            "release_projects": release,
        }
        return {
            "year": year, "month": month, "plan": plan, "kpis": kpis,
            "utilization_by_month": utilization_by_month,
            "signals": strategy_signals(facts, policy),
            "headcount": headcount,
        }
