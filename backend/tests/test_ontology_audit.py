"""Findings of the ontology audit of 2026-11-06 (ontology 8.4.0, requirements baseline D43–D45, build package
docs/ontology-builds/2026-11-06-ontology-audit.yaml).

- D43: a count KPI (在岗人数) was treated as a rate — 35 of 40 people came back as 「完成 35%」 and 「落后 5 个百分点」.
- D44: 「CRM 待同步」 was computed only in the page; it is now Project.crmSyncPendingAmount.
- PM Assistant's tools did not carry the parameter limits of 8.2.0 (D41), so the model guessed and hit 422s.
"""
from dataclasses import replace
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology.derivations.economics import plan_completion
from app.pm_assistant import AssistantSession, _parameter_json

from test_annual_plan_metrics import apply, receive, review
from test_api import settings

TOLERANCE = Decimal("5")


def test_a_count_kpi_is_a_ratio_of_people_not_a_rate():
    headcount = plan_completion(Decimal("35"), Decimal("40"), 10, TOLERANCE, cumulative=False, measure="count")
    assert (headcount["measure"], headcount["completion_percent"], headcount["shortfall"], headcount["status"]) == ("count", 87.5, 5.0, "behind")
    assert "lag_points" not in headcount, "a gap of people is not percentage points"
    rate = plan_completion(Decimal("72"), Decimal("80"), 10, TOLERANCE, cumulative=False)
    assert (rate["measure"], rate["completion_percent"], rate["lag_points"], rate["status"]) == ("rate", 90.0, 8.0, "behind")
    assert "shortfall" not in rate
    cumulative = plan_completion(Decimal("60"), Decimal("100"), 9, TOLERANCE)
    assert (cumulative["measure"], cumulative["completion_percent"], cumulative["lag_points"]) == ("cumulative", 60.0, 15.0), "unchanged"
    over = plan_completion(Decimal("42"), Decimal("40"), 10, TOLERANCE, cumulative=False, measure="count")
    assert (over["completion_percent"], over["shortfall"], over["status"]) == (105.0, -2.0, "on_track")


def test_the_department_review_says_which_kind_each_kpi_is(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert apply(client, "setAnnualOperatingPlan", {"policy": "default", "year": 2026, "headcountTarget": "40",
                                                        "utilizationTargetPercent": "80", "collectionTarget": "1060000"}).status_code == 200
        kpis = review(client)["kpis"]
        assert {key: kpi["measure"] for key, kpi in kpis.items()} == {
            "new_contracts": "cumulative", "collections": "cumulative", "revenue_after_tax": "cumulative", "operating_cost": "cumulative",
            "gross_profit": "cumulative", "gross_margin": "rate", "capacity_used": "cumulative", "utilization": "rate", "headcount": "count"}
        headcount = kpis["headcount"]
        actual = Decimal(headcount["actual"])
        assert headcount["completion_percent"] == float((actual / 40 * 100).quantize(Decimal("0.01")))
        assert headcount["shortfall"] == float(40 - actual) and "lag_points" not in headcount


def test_crm_sync_pending_is_an_ontology_property(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        def project():
            return client.get("/api/v1/ontology/objects/Project/TPL149").json()["data"]["properties"]

        def expected(props):
            return max(Decimal(props["receivedAmount"]) - Decimal(props["crmReceivedAmount"]), Decimal("0"))

        before = project()
        assert Decimal(before["crmSyncPendingAmount"]) == expected(before)
        receive(client, "首期款", "106000", "2026-03-10")
        after = project()
        assert Decimal(after["crmSyncPendingAmount"]) == expected(after)
        assert Decimal(after["receivedAmount"]) > Decimal(after["crmReceivedAmount"]), "the test confirms more than the CRM shows"
        assert Decimal(after["crmSyncPendingAmount"]) > 0
        row = next(r for r in client.get("/api/v1/project-health").json()["data"] if r["project_number"] == "TPL149")
        assert row["crm_sync_pending_amount"] == after["crmSyncPendingAmount"], "the page reads the same figure"
        schema = {t["apiName"]: t for t in client.get("/api/v1/ontology/schema").json()["data"]["objectTypes"]}
        prop = next(p for p in schema["Project"]["properties"] if p["apiName"] == "crmSyncPendingAmount")
        assert (prop["marking"], prop["sourceKind"]) == ("FINANCE", "derived")


def test_crm_sync_pending_is_hidden_without_finance_access(tmp_path):
    with TestClient(create_app(replace(settings(tmp_path), auth_enabled=True))) as client:
        admin = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Tsnt@2026"}).json()["data"]["token"]}
        client.post("/api/v1/users", headers=admin, json={"username": "viewer", "password": "viewer-pass-1", "permissions": ["health"]})
        viewer = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "viewer", "password": "viewer-pass-1"}).json()["data"]["token"]}
        rows = client.get("/api/v1/project-health", headers=viewer).json()["data"]
        assert rows and all(row["crm_sync_pending_amount"] is None for row in rows)
        assert any(row["crm_sync_pending_amount"] is not None for row in client.get("/api/v1/project-health", headers=admin).json()["data"])


def test_assistant_tools_carry_the_declared_limits(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        session = AssistantSession(client.app.state.ontology_service, {"username": "admin", "is_admin": True, "permissions": []})
        tools = {t["name"]: t["parameters"]["properties"] for t in session.tools()}
        assert (tools["resourceForecast"]["months"]["minimum"], tools["resourceForecast"]["months"]["maximum"]) == (1, 6)
        assert tools["departmentOperatingReview"]["year"]["minimum"] == 2000
        assert "YYYY-MM" in tools["resourceHealth"]["month"]["description"]
        texts = [p for params in tools.values() for p in params.values() if p.get("type") == "string" and "enum" not in p]
        schema = client.get("/api/v1/ontology/schema").json()["data"]
        declared = {(f["apiName"], p["apiName"]): p for f in schema["functions"] for p in f["parameters"]}
        for (function, name), param in declared.items():
            if param["dataType"] == "string" and function in tools:
                assert tools[function][name]["maxLength"] == param["maxLength"], (function, name)
        assert texts
    amount = _parameter_json({"apiName": "plannedAmount", "displayName": "约定金额", "dataType": "money", "minimum": 0.0, "maximum": None})
    assert amount["type"] == "string" and "取值 0–不限" in amount["description"] and "最多两位小数" in amount["description"]
    ratio = _parameter_json({"apiName": "paymentRatio", "displayName": "回款比例", "dataType": "percent", "minimum": 0.0, "maximum": 100.0})
    assert "取值 0–100" in ratio["description"]
