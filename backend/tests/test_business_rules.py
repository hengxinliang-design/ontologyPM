"""Business rules and calculation conventions (TSG_PM_业务规则与计算口径说明) and their ontology model."""
import base64
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

from test_api import settings


def entry(external_id, code, work_date, hours="8.00", employee="E001", name="Zhang San"):
    return {
        "external_id": external_id, "project_number": code, "employee_id": employee,
        "employee_name": name, "work_date": work_date, "hours": hours, "approved_hours": hours,
        "approval_status": "approved", "work_content": "投入", "source_updated_at": "2026-07-01T00:00:00+08:00",
    }


def import_entries(client, entries):
    response = client.post("/api/v1/time-entries/import", json=entries)
    assert response.status_code == 200, response.text


def configure(client, code, **values):
    response = client.patch(f"/api/v1/projects/{code}/settings", json=values)
    assert response.status_code == 200, response.text


def health_row(client, code, **period):
    rows = client.get("/api/v1/project-health", params={"project_number": code, **period}).json()["data"]
    assert len(rows) == 1
    return rows[0]


def apply(client, action, parameters):
    return client.post(f"/api/v1/ontology/actions/{action}/apply", json={"parameters": parameters})


def test_one_budget_threshold_set_drives_risk_and_operations(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        configure(client, "TPL149", contract_type="construction", planned_person_days="10",
                  planned_start="2026-01-01", planned_end="2026-12-31", lifecycle_status="active")
        import_entries(client, [
            entry("B1", "TPL149", "2026-03-02", "16.00"), entry("B2", "TPL149", "2026-04-01"),
            entry("B3", "TPL149", "2026-05-04", "24.00"), entry("B4", "TPL149", "2026-06-01", "12.00"),
        ])  # 7.5 of 10 person-days: 75 %

        project = client.get("/api/v1/projects/TPL149").json()["data"]
        assert not any("使用率已达到" in reason for reason in project["risk_reasons"]), "below the default 80 % line"
        assert health_row(client, "TPL149", month="2026-06")["overrun_warning"] == "正常"

        assert client.patch("/api/v1/worktime-baseline", json={"budget_attention_percent": "95"}).status_code == 422
        assert client.patch("/api/v1/worktime-baseline", json={"budget_attention_percent": "70"}).status_code == 200
        project = client.get("/api/v1/projects/TPL149").json()["data"]
        assert "人天预算使用率已达到 70%" in project["risk_reasons"]
        assert health_row(client, "TPL149", month="2026-06")["overrun_warning"] == "需关注", "same line as the risk rule"


def test_trend_compares_with_the_preceding_period_of_equal_length(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        import_entries(client, [
            entry("T1", "TPL149", "2026-03-02", "16.00"), entry("T2", "TPL149", "2026-04-01"),
            entry("T3", "TPL149", "2026-05-04", "24.00"), entry("T4", "TPL149", "2026-06-01", "12.00"),
        ])
        row = health_row(client, "TPL149", start_month="2026-05", end_month="2026-06")
        assert (row["period_actual_person_days"], row["previous_period_person_days"]) == ("4.50", "3.00")
        assert row["previous_period"] == {"start_month": "2026-03", "end_month": "2026-04"}
        assert row["previous_month_person_days"] == "1.00"
        assert row["monthly_trend"] == "上升", "4.5 vs 3.0 over two months each, not 4.5 vs one month"


def test_workload_settlement_skips_budget_rules_but_keeps_milestone_risk(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        configure(client, "TPL149", contract_type="open_ended", planned_person_days="1", lifecycle_status="active")
        import_entries(client, [entry("W1", "TPL149", "2026-05-04", "24.00")])  # 3 person-days against 1
        created = client.post("/api/v1/projects/TPL149/milestones", json={"name": "上线", "planned_end": "2026-01-31"})
        assert created.status_code == 201, created.text

        project = client.get("/api/v1/projects/TPL149").json()["data"]
        assert project["revenue_settlement_basis"] == "workload"
        assert project["outstanding_receivable_amount"] == "74950.00", "invoiced − received, not the framework amount"
        assert not any("预算" in reason for reason in project["risk_reasons"])
        assert project["risk_level"] == "critical"
        assert any(reason.endswith("个里程碑逾期") for reason in project["risk_reasons"]), "milestone rules still apply"
        row = health_row(client, "TPL149", month="2026-05")
        assert row["overrun_warning"] == "工作量结算" and row["health_status"] == "需关注"


def test_employees_are_keyed_by_id_and_busy_people_do_not_lend_internal_time(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        weekdays = [f"2026-03-{day:02d}" for day in range(1, 32)
                    if __import__("datetime").date(2026, 3, day).weekday() < 5]
        busy = [entry(f"O{i}", "TPL149", day, "9.00", employee="E009", name="忙碌") for i, day in enumerate(weekdays)]
        internal = [entry(f"I{i}", "TSG999", day, "2.00", employee="E009", name="忙碌") for i, day in enumerate(weekdays[:2])]
        namesakes = [entry("N1", "TPL149", "2026-03-02", employee="E010", name="王五"),
                     entry("N2", "TPL149", "2026-03-03", employee="E011", name="王五")]
        import_entries(client, busy + internal + namesakes)

        people = client.get("/api/v1/employee-worktime", params={"period": "month", "month": "2026-03"}).json()["data"]
        assert sorted(p["employee_id"] for p in people if p["employee_name"] == "王五") == ["E010", "E011"]

        resources = client.get("/api/v1/resource-operations", params={"month": "2026-03"}).json()["data"]
        busy_row = next(r for r in resources if r["employee_id"] == "E009")
        assert busy_row["allocation_status"] == "本月负荷过高"
        assert busy_row["available_person_days"] == "0.00", "TSG999 time is not lent out by overloaded staff"


def test_expired_opportunity_counts_as_sunk(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        csv = "主题,所有者,状态,可能性\nEXP500-失效商机,张三,失效,30\n"
        assert client.post("/api/v1/crm-imports", json={
            "data_type": "sales_opportunity", "filename": "expired.csv",
            "content_base64": base64.b64encode(csv.encode("utf-8")).decode(),
        }).status_code == 200
        import_entries(client, [entry("X1", "EXP500", "2026-03-02")])
        cost_object = client.get("/api/v1/investment/cost-objects/EXP500").json()["data"]
        assert (cost_object["outcome"], cost_object["outcome_reason"], cost_object["sunk_person_days"]) == ("lost", "失效", "1.00")


def test_ontology_models_payment_terms_operating_policy_and_calendar(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        schema = client.get("/api/v1/ontology/schema").json()["data"]
        types = {t["apiName"]: t for t in schema["objectTypes"]}
        assert schema["version"] == "7.10.0" and {"PaymentTerm", "OperatingPolicy"} <= set(types)
        project_properties = {p["apiName"] for p in types["Project"]["properties"]}
        assert {"category", "lifecycleSource", "revenueSettlementBasis"} <= project_properties
        assert not {"contractStatus", "recognizedRevenueAmount", "confirmedRevenue"} & project_properties, "retired in 3.0.0 and 4.0.0"

        project = client.get("/api/v1/ontology/objects/Project/TPL149").json()["data"]["properties"]
        assert project["lifecycleSource"] == "manual", "the sample project has a PM-maintained lifecycle"

        created = apply(client, "createPaymentTerm", {"project": "TPL149", "name": "首付款", "plannedAmount": "30000", "paymentRatio": "20"})
        assert created.status_code == 200, created.text
        term_id = created.json()["data"]["objects"][0]["primaryKey"]
        term = client.get(f"/api/v1/ontology/objects/PaymentTerm/{term_id}").json()["data"]["properties"]
        assert (term["nodeStatus"], term["plannedAmount"]) == ("unscheduled", "30000.00")
        assert apply(client, "editPaymentTerm", {"paymentTerm": term_id, "plannedAmount": "-1"}).status_code == 422
        assert apply(client, "editPaymentTerm", {"paymentTerm": term_id, "plannedAmount": "35000"}).status_code == 200
        assert client.get(f"/api/v1/ontology/objects/Project/TPL149/links/paymentTerms").json()["data"][0]["primaryKey"] == term_id
        assert apply(client, "deletePaymentTerm", {"paymentTerm": term_id}).status_code == 200

        rejected = apply(client, "updateOperatingPolicy", {"policy": "default", "budgetAttentionPercent": "95"})
        assert rejected.status_code == 422
        assert apply(client, "updateOperatingPolicy", {"policy": "default", "budgetAttentionPercent": "70", "stallDays": 60}).status_code == 200
        policy = client.get("/api/v1/ontology/objects/OperatingPolicy/default").json()["data"]["properties"]
        assert (policy["budgetAttentionPercent"], policy["stallDays"]) == ("70.00", 60)
        assert "失效" in policy["lostStatuses"]

        calendar = client.post("/api/v1/ontology/functions/workingCalendar/execute",
                               json={"parameters": {"startDate": "2026-10-01", "endDate": "2026-10-31"}}).json()["data"]["result"]
        assert calendar["working_days"] == 22 and calendar["available"] is False, "weekday fallback without the subscription"
        assert calendar["baseline_hours"] == "176.00"



def test_resource_sandbox_is_retired(tmp_path):
    """The resource scheduling sandbox was cancelled (ontology 5.0.0); capacity analysis will be rebuilt on the ontology."""
    with TestClient(create_app(settings(tmp_path))) as client:
        functions = {f["apiName"] for f in client.get("/api/v1/ontology/schema").json()["data"]["functions"]}
        assert "resourceSandbox" not in functions and {"resourceHealth", "workingCalendar"} <= functions
        assert client.post("/api/v1/ontology/functions/resourceSandbox/execute", json={"parameters": {}}).status_code == 404
        assert client.get("/api/v1/resource-sandbox").status_code == 404
        assert client.post("/api/v1/resource-sandbox/simulate", json={}).status_code in (404, 405)
        assert client.get("/api/v1/resource-operations").status_code == 200, "resource monitoring stays"

def test_operating_policy_action_edits_lists_and_rejects_out_of_order_thresholds(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        import_entries(client, [entry("P1", "INT001", "2026-03-02")])

        def policy():
            return client.get("/api/v1/ontology/objects/OperatingPolicy/default").json()["data"]["properties"]

        def rejected(parameters, expected):
            response = apply(client, "updateOperatingPolicy", {"policy": "default", **parameters})
            assert response.status_code == 422, response.text
            assert expected in response.json()["detail"], response.json()["detail"]

        # The action form sends lists as text separated by 、; JSON lists work too.
        updated = apply(client, "updateOperatingPolicy", {
            "policy": "default", "lostStatuses": "失败、失效、作废", "internalCodes": ["tsg999", "int001"],
            "standardWorkDaysPerYear": "250"})
        assert updated.status_code == 200, updated.text
        current = policy()
        assert current["lostStatuses"] == ["失败", "失效", "作废"]
        assert current["internalCodes"] == ["TSG999", "INT001"]
        assert current["standardWorkDaysPerYear"] == "250.00"
        cost_object = client.get("/api/v1/investment/cost-objects/INT001").json()["data"]
        assert cost_object["registration"] == "internal", "the new internal code takes effect immediately"

        rejected({"minimumUtilizationPercent": "95"}, "阈值必须逐级递增")
        rejected({"overloadUtilizationPercent": "320"}, "不超过 300%")
        rejected({"budgetHighRiskPercent": "100"}, "小于 100%")
        rejected({"budgetAttentionPercent": "92"}, "预算关注线 92% < 预算高风险线 90%")
        rejected({"standardWorkDaysPerYear": "400"}, "年度参考工作日")
        rejected({"lostStatuses": " 、 "}, "丢单状态至少要有一项")
        assert policy()["budgetAttentionPercent"] == "80.00", "rejected changes are not written"

        # Raising both budget lines together passes, because checks use the submitted values.
        assert apply(client, "updateOperatingPolicy", {
            "policy": "default", "budgetAttentionPercent": "92", "budgetHighRiskPercent": "95"}).status_code == 200
        log = client.get("/api/v1/ontology/action-log", params={"objectType": "OperatingPolicy"}).json()["data"]
        assert [e["status"] for e in log if e["actionType"] == "updateOperatingPolicy"][:2] == ["succeeded", "rejected"]


def test_ontology_metadata_passes_the_skill_lint():
    """Guard the modelling conventions in .claude/skills/tsg-ontology (provenance, markings, write paths, checks)."""
    script = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "tsg-ontology" / "scripts" / "lint_ontology.py"
    spec = importlib.util.spec_from_file_location("lint_ontology", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.lint() == []


def test_worktime_baseline_rest_path_uses_the_same_ranges(tmp_path):
    """The settings page (REST) and the ontology action validate operating parameters identically."""
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.patch("/api/v1/worktime-baseline", json={"standard_hours_per_day": "25"}).status_code == 422
        assert client.patch("/api/v1/worktime-baseline", json={"standard_work_days_per_year": "400"}).status_code == 422
        assert client.patch("/api/v1/worktime-baseline", json={"standard_work_days_per_year": "250"}).status_code == 200

