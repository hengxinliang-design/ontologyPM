"""Declared parameter constraints (ontology 8.2.0, requirements baseline D41, build package
docs/ontology-builds/2026-11-05-parameter-constraints.yaml, test report docs/test-reports/2026-11-01-ontology-browser.md F9–F11).

Invalid input is rejected with a reason instead of being rounded, truncated or quietly replaced.
"""
from fastapi.testclient import TestClient

from app.main import create_app

from test_api import settings
from test_input_guards import apply, failed


def execute(client, function, parameters):
    return client.post(f"/api/v1/ontology/functions/{function}/execute", json={"parameters": parameters})


def test_amounts_with_more_than_two_decimals_are_rejected_not_rounded(tmp_path):
    """F9 (P07): 0.005 was stored as 0.00 and 0.001 was accepted."""
    with TestClient(create_app(settings(tmp_path))) as client:
        for amount in ("0.001", "0.005", "12.345"):
            response = apply(client, "createPaymentTerm", {"project": "WDR101", "name": "x", "plannedAmount": amount}, validate_only=True)
            assert "约定金额：最多保留两位小数" in " ".join(failed(response)), (amount, response.text)
        ratio = apply(client, "createPaymentTerm", {"project": "WDR101", "name": "x", "plannedAmount": "1", "paymentRatio": "33.333"},
                      validate_only=True)
        assert "最多保留两位小数" in " ".join(failed(ratio))
        for amount in ("12.5", "1.000", "100"):
            ok = apply(client, "createPaymentTerm", {"project": "WDR101", "name": "x", "plannedAmount": amount}, validate_only=True)
            assert ok.json()["data"]["valid"] is True, (amount, ok.text)
        before = len(client.get("/api/v1/projects/WDR101").json()["data"]["payment_terms"])
        legacy = client.post("/api/v1/projects/WDR101/payment-terms", json={"name": "尾款", "planned_amount": "0.001"})
        assert legacy.status_code == 422 and "最多保留两位小数" in legacy.text
        assert len(client.get("/api/v1/projects/WDR101").json()["data"]["payment_terms"]) == before
        saved = apply(client, "createPaymentTerm", {"project": "WDR101", "name": "首付款", "plannedAmount": "1.000"})
        assert saved.status_code == 200
        terms = client.get("/api/v1/projects/WDR101").json()["data"]["payment_terms"]
        assert any(t["name"] == "首付款" and t["planned_amount"] == "1.00" for t in terms)


def test_text_parameters_have_a_length_limit(tmp_path):
    """F10 (P14): a 10,000-character name could be saved. Names 200, notes 2000 (D41)."""
    with TestClient(create_app(settings(tmp_path))) as client:
        too_long = apply(client, "createPaymentTerm", {"project": "WDR101", "name": "长" * 201, "plannedAmount": "1"}, validate_only=True)
        assert "名称：最多 200 个字（现有 201 个）" in " ".join(failed(too_long))
        assert apply(client, "createPaymentTerm", {"project": "WDR101", "name": "长" * 200, "plannedAmount": "1"},
                     validate_only=True).json()["data"]["valid"] is True
        notes = {"project": "WDR101", "name": "x", "plannedAmount": "1"}
        assert apply(client, "createPaymentTerm", {**notes, "notes": "备" * 2000}, validate_only=True).json()["data"]["valid"] is True
        assert "备注：最多 2000 个字" in " ".join(failed(apply(client, "createPaymentTerm", {**notes, "notes": "备" * 2001}, validate_only=True)))
        legacy = client.post("/api/v1/projects/WDR101/milestones", json={"name": "里" * 201, "planned_end": "2026-12-31"})
        assert legacy.status_code == 422 and "最多 200 个字" in legacy.text
        # Surrounding spaces do not count (D40 strips them first).
        assert apply(client, "createPaymentTerm", {**notes, "name": "  " + "长" * 200 + "  "}, validate_only=True).json()["data"]["valid"] is True


def test_months_must_be_real_months(tmp_path):
    """F11 (F20): month 2026-13 returned an empty result instead of an error."""
    with TestClient(create_app(settings(tmp_path))) as client:
        for function, parameters in (("resourceHealth", {"month": "2026-13"}), ("resourceHealth", {"month": "2026-1"}),
                                     ("resourceOperations", {"startMonth": "2026-00", "endMonth": "2026-03"}),
                                     ("portfolioHealth", {"month": "26-01"})):
            response = execute(client, function, parameters)
            assert response.status_code == 422 and "月份格式应为 YYYY-MM" in response.text, (function, parameters, response.text)
        assessment = execute(client, "employeeWorktimeAssessment", {"period": "year", "year": "26"})
        assert assessment.status_code == 422 and "年度格式应为 YYYY" in assessment.text
        assert execute(client, "resourceHealth", {"month": "2026-12"}).status_code == 200
        cost = apply(client, "recordOperatingCost", {"policy": "default", "month": "2026-13", "laborCost": "1"}, validate_only=True)
        assert "月份格式应为 YYYY-MM" in " ".join(failed(cost))


def test_integer_parameters_stay_in_their_declared_range(tmp_path):
    """F11 (F10, F26): months=0 was computed as 3 months, pageSize=0 as 50 rows."""
    with TestClient(create_app(settings(tmp_path))) as client:
        for months, reason in ((0, "不能小于 1"), (-1, "不能小于 1"), (7, "不能大于 6")):
            response = execute(client, "resourceForecast", {"months": months})
            assert response.status_code == 422 and f"测算月数：{reason}" in response.text, months
        for months in (1, 6):
            assert len(execute(client, "resourceForecast", {"months": months}).json()["data"]["result"]["months"]) == months
        assert len(execute(client, "resourceForecast", {}).json()["data"]["result"]["months"]) == 3
        for function in ("projectList", "portfolioHealth", "resourceOperations"):
            for size, reason in ((0, "不能小于 1"), (201, "不能大于 200")):
                response = execute(client, function, {"pageSize": size})
                assert response.status_code == 422 and f"每页条数：{reason}" in response.text, (function, size)
        assert execute(client, "projectList", {"pageSize": 200}).status_code == 200
        assert execute(client, "departmentOperatingReview", {"year": 1999}).status_code == 422


def test_unknown_names_are_errors_not_empty_results(tmp_path):
    """F11a: the maintenance worklist returned an empty list for an unknown object type or requirement."""
    with TestClient(create_app(settings(tmp_path))) as client:
        unknown_type = execute(client, "maintenanceWorklist", {"objectType": "Nope"})
        assert unknown_type.status_code == 422 and "对象类型 Nope 没有声明维护要求" in unknown_type.text
        assert execute(client, "maintenanceWorklist", {"requirement": "nope"}).status_code == 422
        assert execute(client, "maintenanceWorklist", {"objectType": "Project"}).status_code == 200


def test_review_month_must_lie_in_the_chosen_year(tmp_path):
    """F11 (F23): year 2026 with month 2025-05 was quietly read as 2026-01."""
    with TestClient(create_app(settings(tmp_path))) as client:
        wrong = execute(client, "departmentOperatingReview", {"year": 2026, "month": "2025-05"})
        assert wrong.status_code == 422 and "截至月份 2025-05 不在 2026 年内" in wrong.text
        legacy = client.get("/api/v1/department/review", params={"year": 2026, "month": "2025-05"})
        assert legacy.status_code == 422
        assert execute(client, "departmentOperatingReview", {"year": 2026, "month": "2026-05"}).json()["data"]["result"]["month"] == "2026-05"
        # Only a year: the default month is still fitted into it (that is not an input error).
        assert execute(client, "departmentOperatingReview", {"year": 2025}).json()["data"]["result"]["month"].startswith("2025-")


def test_schema_publishes_the_constraints(tmp_path):
    """The ontology browser and PM Assistant read the limits from the schema instead of guessing."""
    with TestClient(create_app(settings(tmp_path))) as client:
        schema = client.get("/api/v1/ontology/schema").json()["data"]
        actions = {a["apiName"]: {p["apiName"]: p for p in a["parameters"]} for a in schema["actionTypes"]}
        functions = {f["apiName"]: {p["apiName"]: p for p in f["parameters"]} for f in schema["functions"]}
        assert actions["createPaymentTerm"]["name"]["maxLength"] == 200
        assert actions["createPaymentTerm"]["notes"]["maxLength"] == 2000
        assert actions["createPaymentTerm"]["plannedAmount"]["maxLength"] is None
        assert actions["recordOperatingCost"]["month"]["format"] == "month"
        assert (functions["resourceForecast"]["months"]["minimum"], functions["resourceForecast"]["months"]["maximum"]) == (1, 6)
        every_text = [p for params in (*actions.values(), *functions.values()) for p in params.values() if p["dataType"] == "string"]
        assert every_text and all(p["maxLength"] for p in every_text)
