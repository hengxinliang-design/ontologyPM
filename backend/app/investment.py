"""Investment and sunk-cost analysis by project code (person-day based).

Every time entry is booked against a project code, which acts as the cost
object across the whole lifecycle: pre-sales effort while the code is an
opportunity, delivery effort once a contract is signed.  The code's outcome
decides how pre-sales effort is classified (management-accounting view):

* won (contract signed)  -> effort before the first contract date is
  "cost to win" (converted pre-sales); effort from that date is delivery
* lost / abandoned       -> all effort is sunk cost
* open opportunity       -> effort is at-risk investment (WIP-like exposure)
* internal code          -> internal effort, never project cost
* code missing in CRM    -> unregistered effort (a data-governance todo)

Outcomes come from manual confirmation first, then the CRM: a contract or a
"成功" opportunity means won, configured failure statuses mean lost.  Stalled
opportunities are only flagged; they never become sunk automatically.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from .db import Database
from .project_repository import ProjectRepository, _counts_as_effort, _project_number_from_subject
from .repository import SnapshotRepository


PHASES = (
    ("delivery", "履约成本"),
    ("presales_converted", "获客成本"),
    ("at_risk", "在途投入"),
    ("sunk", "沉没成本"),
    ("internal", "内部投入"),
    ("unregistered", "未建档投入"),
)
PHASE_LABELS = dict(PHASES)
OUTCOME_REASONS = ("竞争失败", "客户取消", "预算不足", "主动放弃", "其他")
AGING_BUCKETS = ((0, 90, "0–3 个月"), (90, 180, "3–6 个月"), (180, 365, "6–12 个月"), (365, None, "12 个月以上"))
ZERO = Decimal("0")


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value)) if value not in (None, "") else ZERO
    except InvalidOperation:
        return ZERO


def _fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(Decimal("0.01")), "f")


def _pct(numerator: Decimal, denominator: Decimal) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator / denominator * 100), 1)


class InvestmentService:
    def __init__(self, database: Database, projects: ProjectRepository, records: SnapshotRepository, timezone: str):
        self.database = database
        self.projects = projects
        self.records = records
        self.timezone = timezone

    # ---------------------------------------------------------------- settings
    def get_settings(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM investment_settings WHERE config_id = 'default'").fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO investment_settings(config_id, updated_at) VALUES ('default', ?)", (self._now(),)
                )
                connection.commit()
                row = connection.execute("SELECT * FROM investment_settings WHERE config_id = 'default'").fetchone()
        return {
            "standard_person_day_cost": row["standard_person_day_cost"],
            "stall_days": row["stall_days"],
            "lost_statuses": json.loads(row["lost_statuses_json"]),
            "internal_codes": json.loads(row["internal_codes_json"]),
            "updated_at": row["updated_at"],
        }

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_settings()
        cost = payload.get("standard_person_day_cost", current["standard_person_day_cost"])
        if cost not in (None, ""):
            if isinstance(cost, bool):
                raise ValueError("standard_person_day_cost must be a positive number")
            try:
                value = Decimal(str(cost))
            except InvalidOperation as exc:
                raise ValueError("standard_person_day_cost must be a positive number") from exc
            if not value.is_finite() or value <= 0:
                raise ValueError("standard_person_day_cost must be a positive number")
            cost = _fmt(value)
        else:
            cost = None
        stall_days = payload.get("stall_days", current["stall_days"])
        if not isinstance(stall_days, int) or isinstance(stall_days, bool) or not 7 <= stall_days <= 730:
            raise ValueError("stall_days must be an integer between 7 and 730")
        lost_statuses = payload.get("lost_statuses", current["lost_statuses"])
        internal_codes = payload.get("internal_codes", current["internal_codes"])
        for name, values in (("lost_statuses", lost_statuses), ("internal_codes", internal_codes)):
            if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
                raise ValueError(f"{name} must be a list of non-empty strings")
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE investment_settings SET standard_person_day_cost=?, stall_days=?,
                    lost_statuses_json=?, internal_codes_json=?, updated_at=?
                WHERE config_id = 'default'
                """,
                (
                    cost, stall_days,
                    json.dumps([v.strip() for v in lost_statuses], ensure_ascii=False),
                    json.dumps([v.strip().upper() for v in internal_codes], ensure_ascii=False),
                    self._now(),
                ),
            )
            connection.commit()
        return self.get_settings()

    # --------------------------------------------------------------- outcomes
    def record_outcome(self, code: str, outcome: str, reason: str, outcome_date: str, notes: str | None, actor: str) -> dict[str, Any]:
        if outcome not in {"lost", "abandoned"}:
            raise ValueError("outcome must be lost or abandoned")
        if reason not in OUTCOME_REASONS:
            raise ValueError(f"reason must be one of {', '.join(OUTCOME_REASONS)}")
        date.fromisoformat(outcome_date)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO opportunity_outcomes(project_code, outcome, reason, outcome_date, notes, recorded_by, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_code) DO UPDATE SET outcome=excluded.outcome, reason=excluded.reason,
                    outcome_date=excluded.outcome_date, notes=excluded.notes,
                    recorded_by=excluded.recorded_by, recorded_at=excluded.recorded_at
                """,
                (code, outcome, reason, outcome_date, notes, actor, self._now()),
            )
            connection.commit()
        return self.cost_object(code) or {"code": code}

    def reopen(self, code: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            cursor = connection.execute("DELETE FROM opportunity_outcomes WHERE project_code = ?", (code,))
            if cursor.rowcount != 1:
                raise KeyError("opportunity outcome not found")
            connection.commit()
        return self.cost_object(code) or {"code": code}

    # ------------------------------------------------------------- inventory
    def cost_objects(self, *, start_month: str | None = None, end_month: str | None = None) -> list[dict[str, Any]]:
        settings = self.get_settings()
        snapshot = self.records.export_records()
        crm = snapshot["records"] if snapshot else []
        with self.database.connect() as connection:
            entries = [
                dict(r) for r in connection.execute("SELECT * FROM time_entries ORDER BY work_date").fetchall()
                if _counts_as_effort(dict(r))
            ]
            outcomes = {r["project_code"]: dict(r) for r in connection.execute("SELECT * FROM opportunity_outcomes").fetchall()}
            hours_per_day = {
                r["project_number"]: _d(r["hours_per_person_day"])
                for r in connection.execute("SELECT project_number, hours_per_person_day FROM project_settings").fetchall()
            }
        default_hours = _d(self.projects.get_worktime_baseline()["standard_hours_per_day"]) or Decimal("8")
        return build_cost_objects(
            crm, entries, outcomes, hours_per_day, settings,
            default_hours=default_hours, today=self._today(),
            start_month=start_month, end_month=end_month,
        )

    def cost_object(self, code: str) -> dict[str, Any] | None:
        return next((item for item in self.cost_objects() if item["code"] == code), None)

    def analysis(self, *, start_month: str | None = None, end_month: str | None = None, include_cost: bool = True) -> dict[str, Any]:
        if (start_month is None) != (end_month is None):
            start_month = start_month or end_month
            end_month = end_month or start_month
        for month in (start_month, end_month):
            if month is not None:
                datetime.strptime(month, "%Y-%m")
        if start_month and end_month and start_month > end_month:
            raise ValueError("start_month must be earlier than or equal to end_month")
        settings = self.get_settings()
        items = self.cost_objects(start_month=start_month, end_month=end_month)
        return summarize(items, settings, start_month=start_month, end_month=end_month, include_cost=include_cost)

    def _today(self) -> date:
        return datetime.now(ZoneInfo(self.timezone)).date()

    def _now(self) -> str:
        return datetime.now(ZoneInfo(self.timezone)).isoformat(timespec="seconds")


def build_cost_objects(
    crm: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    outcomes: dict[str, dict[str, Any]],
    hours_per_day: dict[str, Decimal],
    settings: dict[str, Any],
    *,
    default_hours: Decimal,
    today: date,
    start_month: str | None = None,
    end_month: str | None = None,
) -> list[dict[str, Any]]:
    internal_codes = {code.upper() for code in settings["internal_codes"]}
    lost_statuses = set(settings["lost_statuses"])
    by_code: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"contracts": [], "opportunities": []})
    for record in crm:
        code = record.get("project_number") or _project_number_from_subject(record.get("subject") or "")
        if code:
            by_code[code]["contracts" if record["data_type"] == "contract_order" else "opportunities"].append(record)
    entries_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        entries_by_code[entry["project_number"]].append(entry)

    codes = set(entries_by_code) | set(by_code) | set(outcomes)
    items = [
        _classify(code, by_code.get(code, {"contracts": [], "opportunities": []}), entries_by_code.get(code, []),
                  outcomes.get(code), hours_per_day.get(code) or default_hours, internal_codes, lost_statuses,
                  settings, today, start_month, end_month)
        for code in codes
    ]
    decided = [i for i in items if i["outcome"] in {"won", "lost", "abandoned"}]
    won = sum(i["outcome"] == "won" for i in decided)
    historical = Decimal(won) / Decimal(len(decided)) if decided else None
    for item in items:
        if item["outcome"] != "open":
            continue
        at_risk = _d(item["at_risk_person_days"])
        if item["win_probability"] is not None:
            probability, source = Decimal(item["win_probability"]) / 100, "CRM 赢率"
        elif historical is not None:
            probability, source = historical, "历史赢单率"
        else:
            probability, source = None, None
        item["expected_sunk_person_days"] = _fmt(at_risk * (1 - probability)) if probability is not None else None
        item["probability_source"] = source
    items.sort(key=lambda i: (-_d(i["total_person_days"]), i["code"]))
    return items


def _classify(code, crm, entries, manual, hours_per_day, internal_codes, lost_statuses, settings, today, start_month, end_month):
    contracts, opportunities = crm["contracts"], crm["opportunities"]
    primary = (contracts or opportunities or [{}])[0]
    opportunity = opportunities[0] if opportunities else {}
    flags: list[str] = []
    conversion_date, conversion_source = None, None
    outcome_source, reason, outcome_date = None, None, None
    if code.upper() in internal_codes:
        registration, outcome = "internal", "na"
    elif contracts:
        registration, outcome, outcome_source = "contracted", "won", "合同"
        dates = sorted(r["signed_at"] for r in contracts if r.get("signed_at"))
        if dates:
            conversion_date, conversion_source = dates[0], "首个合同签订日"
        else:
            flags.append("合同缺少签订日期，投入全部计为履约成本")
        if manual:
            flags.append("已签约，忽略人工丢单结论")
    elif manual:
        registration = "opportunity" if opportunities else "unregistered"
        outcome, outcome_source = manual["outcome"], "人工确认"
        reason, outcome_date = manual["reason"], manual["outcome_date"]
    elif opportunities and any(r.get("status") == "成功" for r in opportunities):
        registration, outcome, outcome_source = "opportunity", "won", "CRM 商机成功"
        signed = next((r.get("signed_at") for r in opportunities if r.get("status") == "成功" and r.get("signed_at")), None)
        if signed:
            conversion_date, conversion_source = signed, "商机签单日期"
        else:
            flags.append("商机已成功但尚无合同和签单日期，投入全部计为履约成本")
    elif opportunities and any(r.get("status") in lost_statuses for r in opportunities):
        registration, outcome, outcome_source = "opportunity", "lost", "CRM 状态"
        reason = next(r.get("status") for r in opportunities if r.get("status") in lost_statuses)
    elif opportunities:
        registration, outcome = "opportunity", "open"
    else:
        registration, outcome = "unregistered", "na"

    totals = {phase: ZERO for phase, _ in PHASES}
    monthly: dict[str, dict[str, Decimal]] = defaultdict(lambda: {phase: ZERO for phase, _ in PHASES})
    employees: set[str] = set()
    first_presales: str | None = None
    first_work, last_work = None, None
    in_period = 0
    for entry in entries:
        hours = _d(entry["hours"]) + _d(entry.get("overtime_hours"))
        work_date = entry["work_date"]
        first_work = work_date if first_work is None or work_date < first_work else first_work
        last_work = work_date if last_work is None or work_date > last_work else last_work
        phase = _phase(registration, outcome, conversion_date, work_date)
        if phase in {"presales_converted", "at_risk", "sunk"} and (first_presales is None or work_date < first_presales):
            first_presales = work_date
        month = work_date[:7]
        if (start_month and month < start_month) or (end_month and month > end_month):
            continue
        in_period += 1
        days = hours / hours_per_day if hours_per_day else ZERO
        totals[phase] += days
        monthly[month][phase] += days
        employees.add(entry["employee_id"])

    win_probability = opportunity.get("probability")
    if outcome == "open" and win_probability is None:
        flags.append("CRM 未填写赢率")
    stall_days = settings["stall_days"]
    stalled = (
        outcome == "open" and last_work is not None
        and (today - date.fromisoformat(last_work)).days > stall_days
    )
    age = (today - date.fromisoformat(first_presales)).days if outcome == "open" and first_presales else None
    total = sum(totals.values(), ZERO)
    presales = totals["presales_converted"] + totals["at_risk"] + totals["sunk"]
    return {
        "code": code,
        "subject": primary.get("subject"),
        "customer": primary.get("customer"),
        "crm_owner": primary.get("owner"),
        "crm_status": opportunity.get("status"),
        "registration": registration,
        "outcome": outcome,
        "outcome_source": outcome_source,
        "outcome_reason": reason,
        "outcome_date": outcome_date,
        "outcome_recorded_by": manual["recorded_by"] if manual else None,
        "win_probability": win_probability,
        "contract_value": _fmt(sum((_d((r.get("total_amount") or {}).get("amount") if isinstance(r.get("total_amount"), dict) else r.get("total_amount")) for r in contracts), ZERO)) if contracts else None,
        "conversion_date": conversion_date,
        "conversion_date_source": conversion_source,
        "hours_per_person_day": _fmt(hours_per_day),
        "first_work_date": first_work,
        "last_work_date": last_work,
        "entry_count": in_period,
        "employee_count": len(employees),
        "total_person_days": _fmt(total),
        "presales_person_days": _fmt(presales),
        "presales_converted_person_days": _fmt(totals["presales_converted"]),
        "delivery_person_days": _fmt(totals["delivery"]),
        "at_risk_person_days": _fmt(totals["at_risk"]),
        "sunk_person_days": _fmt(totals["sunk"]),
        "internal_person_days": _fmt(totals["internal"]),
        "unregistered_person_days": _fmt(totals["unregistered"]),
        "expected_sunk_person_days": None,
        "probability_source": None,
        "cost_to_win_share": _pct(totals["presales_converted"], totals["presales_converted"] + totals["delivery"]) if outcome == "won" else None,
        "presales_age_days": age,
        "stalled": stalled,
        "monthly": [{"month": m, **{k: _fmt(v) for k, v in values.items()}} for m, values in sorted(monthly.items())],
        "data_flags": flags,
    }


def _phase(registration: str, outcome: str, conversion_date: str | None, work_date: str) -> str:
    if registration == "internal":
        return "internal"
    if outcome == "won":
        return "presales_converted" if conversion_date and work_date < conversion_date else "delivery"
    if outcome in {"lost", "abandoned"}:
        return "sunk"
    if outcome == "open":
        return "at_risk"
    return "unregistered"


def summarize(items: list[dict[str, Any]], settings: dict[str, Any], *, start_month, end_month, include_cost: bool) -> dict[str, Any]:
    totals = {phase: sum((_d(i[f"{phase}_person_days"]) for i in items), ZERO) for phase, _ in PHASES}
    grand = sum(totals.values(), ZERO)
    expected = sum((_d(i["expected_sunk_person_days"]) for i in items if i["expected_sunk_person_days"]), ZERO)
    won = [i for i in items if i["outcome"] == "won"]
    lost = [i for i in items if i["outcome"] in {"lost", "abandoned"}]
    open_items = [i for i in items if i["outcome"] == "open"]
    rate = _d(settings["standard_person_day_cost"]) if settings["standard_person_day_cost"] else None

    def cost(days: Decimal) -> str | None:
        return _fmt(days * rate) if include_cost and rate is not None else None

    monthly: dict[str, dict[str, Decimal]] = defaultdict(lambda: {phase: ZERO for phase, _ in PHASES})
    for item in items:
        for row in item["monthly"]:
            for phase, _ in PHASES:
                monthly[row["month"]][phase] += _d(row[phase])

    aging = []
    for low, high, label in AGING_BUCKETS:
        bucket = [i for i in open_items if i["presales_age_days"] is not None and i["presales_age_days"] >= low and (high is None or i["presales_age_days"] < high)]
        aging.append({"bucket": label, "count": len(bucket), "person_days": _fmt(sum((_d(i["at_risk_person_days"]) for i in bucket), ZERO))})

    reasons: dict[str, dict[str, Any]] = {}
    for item in lost:
        key = item["outcome_reason"] or "未注明"
        entry = reasons.setdefault(key, {"reason": key, "count": 0, "person_days": ZERO})
        entry["count"] += 1
        entry["person_days"] += _d(item["sunk_person_days"])

    converted = totals["presales_converted"]
    sunk = totals["sunk"]
    return {
        "period": {"start_month": start_month, "end_month": end_month},
        "settings": {**settings, "standard_person_day_cost": settings["standard_person_day_cost"] if include_cost else None},
        "cost_enabled": include_cost and rate is not None,
        "totals": {
            "total_person_days": _fmt(grand),
            **{f"{phase}_person_days": _fmt(value) for phase, value in totals.items()},
            "expected_sunk_person_days": _fmt(expected),
            "total_cost": cost(grand),
            "sunk_cost": cost(sunk),
            "at_risk_cost": cost(totals["at_risk"]),
        },
        "structure": [
            {"phase": phase, "label": label, "person_days": _fmt(totals[phase]), "share": _pct(totals[phase], grand), "cost": cost(totals[phase])}
            for phase, label in PHASES
        ],
        "ratios": {
            "won_count": len(won),
            "lost_count": len(lost),
            "open_count": len(open_items),
            "unregistered_count": sum(i["registration"] == "unregistered" and i["outcome"] == "na" for i in items),
            "win_rate_by_count": _pct(Decimal(len(won)), Decimal(len(won) + len(lost))),
            "win_rate_by_effort": _pct(converted, converted + sunk),
            "sunk_rate": _pct(sunk, converted + sunk),
            "average_cost_to_win_person_days": _fmt(converted / len(won)) if won else None,
            "cost_to_win_share": _pct(converted, converted + totals["delivery"]),
            "effective_investment_share": _pct(totals["delivery"] + converted, grand),
        },
        "aging": aging,
        "monthly": [{"month": m, **{k: _fmt(v) for k, v in values.items()}} for m, values in sorted(monthly.items())],
        "open_opportunities": sorted(
            [_brief(i) for i in open_items], key=lambda i: -_d(i["at_risk_person_days"])
        ),
        "sunk_items": sorted([_brief(i) for i in lost], key=lambda i: -_d(i["sunk_person_days"])),
        "sunk_reasons": sorted(
            [{**r, "person_days": _fmt(r["person_days"])} for r in reasons.values()], key=lambda r: -_d(r["person_days"])
        ),
        "unregistered": sorted(
            [_brief(i) for i in items if i["registration"] == "unregistered" and i["outcome"] == "na" and _d(i["unregistered_person_days"]) > 0],
            key=lambda i: -_d(i["unregistered_person_days"]),
        ),
        "won_items": sorted([_brief(i) for i in won], key=lambda i: -_d(i["presales_converted_person_days"])),
    }


def _brief(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k != "monthly"}
