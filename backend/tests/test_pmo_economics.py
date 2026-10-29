"""Department PMO economics (rule cards: docs/ontology-builds/2026-10-26-pmo-economics.yaml).

Person-day economics is paused in ontology 4.0.0 (docs/ontology-builds/2026-10-27-pause-economics.yaml): the
finance inputs are no longer maintained and nothing on the pages depends on them.  The pure functions stay and
keep reproducing finance's SEY operating sheet (as of 2026-09-30), so the rules are ready when economics returns.
The API tests cover what the department review still does without economics, and that the pause holds.
"""
import base64
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.db import POLICY_COLUMN_DEFAULTS
from app.ontology.derivations.economics import (
    backlog_coverage,
    compute_project_economics,
    plan_completion,
    resource_health,
    rollup,
    strategy_signals,
)
from app.main import create_app

from test_api import settings

POLICY = {
    **POLICY_COLUMN_DEFAULTS,
    "attention_utilization_percent": "60.00",
    "minimum_utilization_percent": "80.00",
    "excellent_utilization_percent": "90.00",
    "overload_utilization_percent": "100.00",
}
AS_OF = date(2026, 9, 30)
RATE = Decimal("2000")


def economics(contract_value, *, paid="0", used_days="0", crm_received=None, settings=None, payments=None,
              planned_days=None, lifecycle="active", today=AS_OF, contract_type="construction"):
    if payments is None:
        payments = [{"status": "paid", "actual_amount": paid, "planned_date": "2026-03-31"}] if Decimal(paid) else []
    return compute_project_economics(
        contract_value=Decimal(contract_value), invoiced_amount=Decimal("0"),
        crm_received_amount=Decimal(crm_received if crm_received is not None else paid),
        contract_type=contract_type, workload_settlement=False, lifecycle=lifecycle,
        used_person_days=Decimal(used_days), planned_person_days=Decimal(planned_days) if planned_days else None,
        payments=payments, settings={"contract_person_day_rate": str(RATE), **(settings or {})},
        policy=POLICY, today=today,
    )


def days_for(cost: str) -> str:
    """Finance's incurred cost expressed as person-days at the example rate."""
    return str(Decimal(cost) / RATE)


def sey():
    """SEY101 / SEY103 / SEY104 from the finance sheet: contract value, collected, incurred cost."""
    return {
        "SEY101": economics("1254086.00", paid="627043.00", used_days=days_for("1592625.54")),
        "SEY103": economics("700000.00", paid="350000.00", used_days=days_for("247451.97")),
        "SEY104": economics("172250.00", paid="51675.00", used_days="0"),
    }


def with_numbers(items):
    return [{"project_number": code, **item} for code, item in items.items()]


# ------------------------------------------------------------------ project economics (R01–R10)
def test_estimated_revenue_matches_finance_example():
    projects = sey()
    assert projects["SEY101"]["estimated_revenue"] == "1183100.00"
    assert rollup(with_numbers(projects))["estimated_revenue"] == "2005977.36"
    deducted = economics("1254086.00", settings={"external_procurement_amount": "100000", "commercial_expense_amount": "3100"})
    assert deducted["estimated_revenue"] == "1080000.00", "external procurement and commercial fees are deducted"
    goods = economics("1130000.00", settings={"tax_rate_percent": "13"})
    assert goods["estimated_revenue"] == "1000000.00" and goods["tax_rate_percent"] == 13.0
    negative = economics("106000.00", settings={"external_procurement_amount": "150000"})
    assert negative["estimated_revenue"] == "-50000.00" and negative["status"] == "exceeded"
    assert any("扣减项超过税后合同额" in flag for flag in negative["data_flags"])


def test_available_cost_uses_target_margin_with_project_override():
    assert sey()["SEY101"]["available_cost"] == "828170.00"
    overridden = economics("1254086.00", settings={"target_gross_margin_percent": "20", "target_margin_reason": "战略首单"})
    assert overridden["available_cost"] == "946480.00"
    assert overridden["target_margin_overridden"] and overridden["target_margin_reason"] == "战略首单"


def test_disposable_person_days_from_contract_rate():
    assert sey()["SEY101"]["disposable_person_days"] == "414.09"
    no_rate = economics("1254086.00", settings={"contract_person_day_rate": None})
    assert no_rate["disposable_person_days"] is None and no_rate["status"] == "no_baseline"
    assert no_rate["reasons"] == ["缺少合同人天单价"]
    assert economics("0")["status"] == "no_baseline", "no contract value, no baseline"


def test_estimated_margin_matches_finance_example():
    projects = sey()
    assert projects["SEY101"]["estimated_gross_margin_percent"] == -34.61
    assert projects["SEY103"]["estimated_gross_margin_percent"] == 62.53
    assert rollup(with_numbers(projects))["estimated_gross_margin_percent"] == 8.27
    estimated = economics("700000.00", used_days="100", settings={"remaining_person_days_estimate": "150"})
    # (660,377.36 − 250 × 2,000) ÷ 660,377.36
    assert estimated["forecast_gross_margin_percent"] == 24.29 and estimated["remaining_source"] == "项目经理估算"
    fallback = economics("700000.00", used_days="100", planned_days="160")
    assert fallback["remaining_person_days"] == "60.00" and fallback["remaining_source"] == "预算人天 − 已用人天"
    assert any("剩余人天估算缺失" in flag for flag in fallback["data_flags"])
    closed = economics("700000.00", used_days="100", planned_days="160", settings={"remaining_person_days_estimate": "50"},
                       lifecycle="closed")
    assert closed["remaining_person_days"] == "0.00", "a finished project has no remaining effort"


def test_confirmed_revenue_from_paid_milestone_nodes():
    projects = sey()
    assert projects["SEY101"]["confirmed_revenue"] == "591550.00"
    assert rollup(with_numbers(projects))["confirmed_revenue"] == "970488.68"
    pending = economics("700000.00", payments=[
        {"status": "paid", "actual_amount": "350000.00", "planned_date": "2026-03-31"},
        {"status": "planned", "actual_amount": "0.00", "planned_date": "2026-12-31"},
    ])
    assert pending["confirmed_revenue"] == "330188.68", "an unpaid node is not recognised"
    deductions = economics("1060000.00", paid="530000.00", settings={"external_procurement_amount": "100000"})
    # 530,000 ÷ 1.06 − 100,000 × 50 %
    assert deductions["confirmed_revenue"] == "450000.00"
    assert economics("700000.00", contract_type="unclassified")["confirmed_revenue"] is None


def test_confirmed_margin_is_reported_but_not_decisive():
    projects = sey()
    assert rollup(with_numbers(projects))["confirmed_gross_margin_percent"] == -89.60
    assert projects["SEY103"]["confirmed_gross_margin_percent"] == 25.06, "below the 30 % target"
    assert projects["SEY103"]["status"] == "on_target", "the confirmed margin alone never decides the status"
    nothing_paid = economics("700000.00", used_days="10")
    assert nothing_paid["confirmed_gross_margin_percent"] is None, "no confirmed revenue yet"


def test_cost_ahead_flags_strict_monitoring():
    projects = sey()
    assert projects["SEY101"]["cost_progress_percent"] == 192.31
    assert projects["SEY101"]["revenue_progress_percent"] == 50.0
    assert projects["SEY101"]["cost_ahead_points"] == 142.31
    assert projects["SEY103"]["cost_ahead_points"] == 3.53 and projects["SEY103"]["status"] == "on_target"

    # 350,000 of 700,000 available cost spent, nothing collected: 50 points ahead.
    nodes = [{"status": "planned", "actual_amount": "0.00", "planned_date": "2026-11-30"}]
    in_grace = economics("1060000.00", used_days="175", payments=nodes)
    assert in_grace["cost_ahead_points"] == 50.0 and in_grace["in_grace_period"]
    assert in_grace["status"] == "on_target" and any("宽限期" in flag for flag in in_grace["data_flags"])
    after_grace = economics("1060000.00", used_days="175", payments=nodes, today=date(2026, 12, 1))
    assert after_grace["status"] == "exceeded" and "成本超前 50.00 个百分点" in after_grace["reasons"][0]
    no_nodes = economics("1060000.00", used_days="175", payments=[])
    assert no_nodes["status"] == "exceeded", "without payment nodes there is no grace period"


def test_economics_status_order():
    projects = sey()
    assert projects["SEY101"]["status"] == "exceeded"
    assert projects["SEY101"]["reasons"][0] == "动态成本 1592625.54 超过可用成本 828170.00"
    assert projects["SEY103"]["status"] == "on_target"
    assert economics("700000.00", settings={"contract_person_day_rate": ""})["status"] == "no_baseline"
    warning = economics("700000.00", paid="350000.00", used_days="100", settings={"remaining_person_days_estimate": "150"})
    assert warning["status"] == "warning" and warning["reasons"] == ["预计完工毛利率 24.29% 低于目标 30.0%"]
    both = economics("1254086.00", paid="627043.00", used_days=days_for("1592625.54"),
                     settings={"remaining_person_days_estimate": "10"})
    assert both["status"] == "exceeded" and len(both["reasons"]) == 3, "exceeded wins, every reason is listed"


def test_payment_reconciliation_warning():
    item = economics("1254086.00", paid="600000.00", crm_received="627043.00")
    assert any(flag.startswith("回款对账差异 27043.00") for flag in item["data_flags"])
    assert item["confirmed_revenue"] == "566037.74", "milestone nodes stay authoritative"
    assert not economics("1254086.00", paid="627043.00")["data_flags"]


# ------------------------------------------------------------------ API: inputs, write paths, rollups
def csv_upload(client, rows):
    content = "项目编号,主题,对应客户,我方签约人,签约时间,分类,状态,总金额,回款金额,开票金额\n" + "\n".join(rows) + "\n"
    response = client.post("/api/v1/crm-imports", json={
        "data_type": "contract_order", "filename": "sey.csv",
        "content_base64": base64.b64encode(content.encode()).decode(),
    })
    assert response.status_code == 200, response.text


def configure(client, code, **values):
    response = client.patch(f"/api/v1/projects/{code}/settings", json=values)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def entry(external_id, code, work_date, hours="8.00", overtime="0.00", employee="E001", name="Zhang San"):
    return {
        "external_id": external_id, "project_number": code, "employee_id": employee, "employee_name": name,
        "work_date": work_date, "hours": hours, "overtime_hours": overtime, "approved_hours": hours,
        "approval_status": "submitted", "work_content": "实施", "source_updated_at": "2026-09-30T00:00:00+08:00",
    }


def import_entries(client, entries):
    response = client.post("/api/v1/time-entries/import", json=entries)
    assert response.status_code == 200, response.text


def apply(client, action, parameters):
    return client.post(f"/api/v1/ontology/actions/{action}/apply", json={"parameters": parameters})


def workdays(month: str, count: int):
    day, result = date(int(month[:4]), int(month[5:]), 1), []
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day = date.fromordinal(day.toordinal() + 1)
    return result


# ------------------------------------------------------------------ resources (R12, R13)
def month(project_hours="0", internal="0", overtime="0", projects=1):
    return {"project_hours": Decimal(project_hours), "internal_hours": Decimal(internal),
            "overtime_hours": Decimal(overtime), "projects": {f"P{i}" for i in range(projects)}}


CAPACITY = {"2026-07": Decimal("160"), "2026-08": Decimal("160"), "2026-09": Decimal("160")}


def test_resource_health_statuses():
    overloaded = resource_health({"2026-08": month("176"), "2026-09": month("176")}, "2026-09", CAPACITY, POLICY)
    assert overloaded["status"] == "overloaded" and overloaded["consecutive_overload_months"] == 2
    one_month = resource_health({"2026-08": month("120"), "2026-09": month("176")}, "2026-09", CAPACITY, POLICY)
    assert one_month["status"] == "healthy", "a single overloaded month is not yet a risk"
    gap = resource_health({"2026-07": month("176"), "2026-09": month("176")}, "2026-09", CAPACITY, POLICY)
    assert gap["consecutive_overload_months"] == 1, "a month without hours breaks the streak"
    stale = resource_health({"2026-07": month("176"), "2026-08": month("176")}, "2026-09", CAPACITY, POLICY)
    assert stale["status"] == "healthy" and stale["consecutive_overload_months"] == 0, "past overload is not this month's risk"

    fatigue = resource_health({"2026-08": month("150"), "2026-09": month("120", internal="40", overtime="40")},
                              "2026-09", CAPACITY, POLICY)
    assert fatigue["status"] == "fatigue" and fatigue["overtime_share_percent"] == 25.0, "internal hours are in the denominator"
    fragmented = resource_health({"2026-08": month("150"), "2026-09": month("150", projects=5)}, "2026-09", CAPACITY, POLICY)
    assert fragmented["status"] == "fragmented" and fragmented["concurrent_project_count"] == 5

    idle = resource_health({"2026-08": month("150"), "2026-09": month("80")}, "2026-09", CAPACITY, POLICY)
    assert idle["status"] == "idle" and idle["utilization_percent"] == 50.0
    first = resource_health({"2026-09": month("80")}, "2026-09", CAPACITY, POLICY)
    assert first["status"] == "healthy" and first["first_month"], "the first month is never idle"
    internal_only = resource_health({"2026-08": month("150"), "2026-09": month("0", internal="160", projects=0)},
                                    "2026-09", CAPACITY, POLICY)
    assert internal_only["status"] == "idle", "TSG999 hours do not count as project utilisation"
    several = resource_health({"2026-08": month("176"), "2026-09": month("176", internal="20", overtime="60", projects=5)},
                              "2026-09", CAPACITY, POLICY)
    assert several["statuses"] == ["overloaded", "fatigue", "fragmented"], "the most severe status leads"


def test_release_signal_for_burnt_out_projects(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        csv_upload(client, ["SEY104,SEY104-系统实施,SEY,Sales A,2026-01-05,系统实施,执行中,172250.00,0.00,0.00"])
        configure(client, "SEY104", planned_person_days="100", planned_start="2026-01-05", planned_end="2026-10-31")
        # Planned to end in the month after the review month while people still report hours in it.
        import_entries(client, [entry(f"R{i}", "SEY104", day, hours="8.00") for i, day in enumerate(workdays("2026-09", 5))])
        review = client.get("/api/v1/department/review", params={"year": 2026, "month": "2026-09"}).json()["data"]
        release = next(s for s in review["signals"] if s["signal"] == "release_resources")
        assert release["evidence"] == ["SEY104：计划于 2026-10-31 结束，人员可陆续释放"]
        health = client.get("/api/v1/department/resource-health", params={"month": "2026-09"}).json()["data"]
        assert {row["employee_name"] for row in health["data"]} == {"Zhang San"}
        employee = client.get("/api/v1/ontology/objects/Employee/E001").json()["data"]["properties"]
        assert employee["healthStatus"] in {"idle", "healthy"} and employee["concurrentProjectCount"] == 1


# ------------------------------------------------------------------ department review (R14–R16)
def test_annual_plan_completion_against_time_progress(tmp_path):
    behind = plan_completion(Decimal("60"), Decimal("100"), 9, Decimal("5"))
    assert behind["status"] == "behind" and behind["time_progress_percent"] == 75.0 and behind["lag_points"] == 15.0
    assert plan_completion(Decimal("72"), Decimal("100"), 9, Decimal("5"))["status"] == "on_track", "within the 5-point tolerance"
    assert plan_completion(Decimal("72"), None, 9, Decimal("5"))["status"] == "no_target"
    assert plan_completion(None, Decimal("100"), 9, Decimal("5"))["status"] == "no_data"
    assert plan_completion(Decimal("28"), Decimal("30"), 9, Decimal("5"), cumulative=False)["status"] == "behind", \
        "a rate is compared with its target, not with time progress"

    with TestClient(create_app(settings(tmp_path))) as client:
        review = client.get("/api/v1/department/review", params={"year": 2026, "month": "2026-09"}).json()["data"]
        assert review["plan"] is None and review["kpis"]["collections"]["status"] in {"no_target", "no_data"}
        saved = apply(client, "setAnnualOperatingPlan", {
            "policy": "default", "year": 2026, "collectionTarget": "1000000", "newContractTarget": "2000000", "headcountTarget": "12",
        })
        assert saved.status_code == 200, saved.text
        assert apply(client, "setAnnualOperatingPlan", {"policy": "default", "year": 2026, "utilizationTargetPercent": "120"}).status_code == 422
        assert apply(client, "setAnnualOperatingPlan", {"policy": "default", "year": 1999}).status_code == 422
        plan = client.get("/api/v1/ontology/objects/AnnualOperatingPlan/2026-实施交付部").json()["data"]["properties"]
        assert plan["collectionTarget"] == "1000000.00" and plan["headcountTarget"] == "12.00"
        review = client.get("/api/v1/department/review", params={"year": 2026, "month": "2026-09"}).json()["data"]
        assert review["kpis"]["collections"]["target"] == "1000000.00"
        assert review["kpis"]["collections"]["time_progress_percent"] == 75.0
        rest = client.put("/api/v1/department/annual-plans/2026", json={"new_contract_target": "2500000"})
        assert rest.status_code == 200 and rest.json()["data"]["collection_target"] == "1000000.00", "unchanged fields are kept"


def facts(**overrides):
    return {
        "acceptance_projects": [], "exceeded_projects": [], "forecast_margin_percent": 35.0,
        "demand_next3_days": 100.0, "capacity_next3_days": 500.0, "recent_utilization": [80.0, 80.0],
        "backlog_coverage_percent": 80.0, "release_projects": [], **overrides,
    }


def test_strategy_signals_priority_and_evidence():
    assert [s["signal"] for s in strategy_signals(facts(), POLICY)] == ["hold"]
    signals = strategy_signals(facts(
        acceptance_projects=[("SEY103", "1 个回款节点逾期未到账")],
        exceeded_projects=["SEY101", "TPL149"], forecast_margin_percent=8.27,
    ), POLICY)
    assert [s["signal"] for s in signals] == ["push_acceptance", "control_cost"]
    assert signals[0]["evidence"] == ["SEY103：1 个回款节点逾期未到账"]
    assert signals[1]["evidence"] == ["2 个项目超标：SEY101、TPL149", "部门预计完工毛利率 8.27% 低于目标 30.0%"]
    one_exceeded = strategy_signals(facts(exceeded_projects=["SEY101"]), POLICY)
    assert [s["signal"] for s in one_exceeded] == ["hold"], "a single exceeded project is handled on the project"

    staffing = strategy_signals(facts(demand_next3_days=600.0, recent_utilization=[95.0, 97.0]), POLICY)
    assert staffing[0]["signal"] == "staffing_support" and len(staffing[0]["evidence"]) == 2
    busy_but_covered = strategy_signals(facts(demand_next3_days=600.0, recent_utilization=[85.0, 97.0]), POLICY)
    assert [s["signal"] for s in busy_but_covered] == ["hold"], "both recent months must exceed the excellent line"


def test_backlog_coverage_signal():
    assert backlog_coverage(Decimal("3000"), Decimal("8000")) == Decimal("37.5")
    assert backlog_coverage(Decimal("3000"), Decimal("0")) is None
    signals = strategy_signals(facts(backlog_coverage_percent=37.5), POLICY)
    assert signals[0]["signal"] == "win_contracts"
    assert signals[0]["evidence"] == ["在手合同覆盖明年产能 37.5%，低于覆盖线 50.0%"]
    idle = strategy_signals(facts(recent_utilization=[70.0, 55.0]), POLICY)
    assert idle[0]["signal"] == "win_contracts" and "不高于关注线" in idle[0]["evidence"][0]


# ------------------------------------------------------------------ pause (ontology 4.0.0)
PAUSED_PROJECT_PROPERTIES = {
    "contractPersonDayRate", "taxRatePercent", "externalProcurementAmount", "commercialExpenseAmount",
    "targetGrossMarginPercent", "targetMarginReason", "remainingPersonDaysEstimate", "estimatedRevenue",
    "availableCost", "disposablePersonDays", "dynamicCost", "confirmedRevenue", "economicsStatus",
}


def test_person_day_economics_is_paused(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        schema = client.get("/api/v1/ontology/schema").json()["data"]
        types = {t["apiName"]: t for t in schema["objectTypes"]}
        assert not PAUSED_PROJECT_PROPERTIES & {p["apiName"] for p in types["Project"]["properties"]}
        assert "projectEconomics" not in types["Project"]["derivations"]
        functions = {f["apiName"] for f in schema["functions"]}
        assert not {"projectEconomics", "customerEconomics", "portfolioEconomics", "customerEconomicsRollup"} & functions
        configure_params = {p["apiName"] for p in next(a for a in schema["actionTypes"] if a["apiName"] == "configureProject")["parameters"]}
        assert "contractPersonDayRate" not in configure_params

        csv_upload(client, ["SEY101,SEY101-系统实施,SEY,Sales A,2026-01-05,系统实施,执行中,1254086.00,0.00,0.00"])
        configure(client, "SEY101", planned_person_days="300", contract_person_day_rate="2000", tax_rate_percent="13")
        project = client.get("/api/v1/projects/SEY101").json()["data"]
        assert "economics" not in project and project["settings"]["contract_person_day_rate"] is None, \
            "the base program no longer maintains finance inputs"
        row = client.get("/api/v1/project-health", params={"month": "2026-09"}).json()["data"][0]
        assert "economics" not in row
        review = client.get("/api/v1/department/review", params={"year": 2026, "month": "2026-09"}).json()["data"]
        assert set(review["kpis"]) == {"new_contracts", "collections", "capacity_used", "utilization", "headcount"}
        assert not {"portfolio", "backlog", "demand", "forecast_gross_margin_percent"} & set(review)
        assert not {"control_cost", "staffing_support"} & {s["signal"] for s in review["signals"]}
        assert client.get("/api/v1/department/customers").status_code == 404
        assert client.get("/api/v1/department/projects").status_code == 404

