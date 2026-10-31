"""Annual operating plan metrics (ontology 7.10.0, build package docs/ontology-builds/2026-10-31-annual-plan-metrics.yaml).

Operating revenue = receipts confirmed in the year (before and after VAT), contract value = contracts signed in the
year, operating cost = finance figures entered per month, gross profit and margin up to the latest month with costs
(requirements baseline D22–D29).
"""
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology.derivations.economics import plan_completion

from test_api import settings


def apply(client, action, parameters, *, validate_only=False):
    return client.post(f"/api/v1/ontology/actions/{action}/apply", json={"parameters": parameters, "validateOnly": validate_only})


def review(client, month="2026-10"):
    response = client.get("/api/v1/department/review", params={"year": 2026, "month": month})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def receive(client, name, amount, actual_date):
    nodes = {n["name"]: n["payment_node_id"] for n in client.get("/api/v1/projects/TPL149").json()["data"]["payment_nodes"]}
    response = apply(client, "confirmPaymentReceived", {"paymentNode": nodes[name], "actualAmount": amount, "actualDate": actual_date})
    assert response.status_code == 200, response.text


def cost(client, month, *, validate_only=False, **amounts):
    return apply(client, "recordOperatingCost", {"policy": "default", "month": month, **amounts}, validate_only=validate_only)


def test_revenue_contract_value_and_tax(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        receive(client, "首期款", "106000", "2026-03-10")
        kpis = review(client)["kpis"]
        assert kpis["collections"]["label"] == "经营收入（税前）" and kpis["collections"]["actual"] == "106000.00"
        assert kpis["revenue_after_tax"]["actual"] == "100000.00", "106,000 ÷ 1.06"

        snapshot = client.app.state.repository.export_records()["records"]
        signed_2026 = sum((Decimal((r["total_amount"] or {}).get("amount") or "0") for r in snapshot
                           if r["data_type"] == "contract_order" and (r.get("signed_at") or "")[:4] == "2026"
                           and r["signed_at"] <= "2026-10-31"), Decimal("0"))
        assert kpis["new_contracts"]["label"] == "合同额"
        assert Decimal(kpis["new_contracts"]["actual"]) == signed_2026, "contracts signed in the year; opportunities do not count"

        assert apply(client, "updateOperatingPolicy", {"policy": "default", "defaultTaxRatePercent": "9"}).status_code == 200
        assert review(client)["kpis"]["revenue_after_tax"]["actual"] == "97247.71", "the VAT rate is a parameter"
        policy = client.get("/api/v1/ontology/objects/OperatingPolicy/default").json()["data"]["properties"]
        assert Decimal(policy["defaultTaxRatePercent"]) == Decimal("9")


def test_operating_cost_entry(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        first = cost(client, "2026-09", laborCost="300000", bonusCost="50000", travelCost="20000", outsourcingCost="80000",
                     trainingCost="5000", otherCost="15000", notes="财务 9 月报表")
        assert first.status_code == 200, first.text
        row = client.get("/api/v1/ontology/objects/OperatingCost/2026-09-实施交付部").json()["data"]["properties"]
        assert (row["totalCost"], row["laborCost"], row["updatedBy"]) == ("470000.00", "300000.00", "test")

        assert cost(client, "2026-09", laborCost="310000").status_code == 200
        row = client.get("/api/v1/ontology/objects/OperatingCost/2026-09-实施交付部").json()["data"]["properties"]
        assert (row["laborCost"], row["bonusCost"], row["totalCost"]) == ("310000.00", "0.00", "310000.00"), "re-entering replaces the month"
        log = client.get("/api/v1/ontology/action-log", params={"objectType": "OperatingCost"}).json()["data"]
        assert len([e for e in log if e["actionType"] == "recordOperatingCost" and e["status"] == "succeeded"]) == 2

        negative = cost(client, "2026-08", travelCost="-1", validate_only=True)  # visible already in the dry run
        assert negative.status_code == 200 and negative.json()["data"]["valid"] is False and "金额不能为负数" in negative.text
        future = cost(client, "2099-01", laborCost="1")
        assert future.status_code == 422 and "之后的月份" in future.text
        assert cost(client, "2026-13", laborCost="1").status_code == 422


def test_gross_profit_uses_the_cost_cutoff(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        receive(client, "首期款", "106000", "2026-03-10")
        receive(client, "验收尾款", "53000", "2026-05-20")
        empty = review(client)
        assert empty["operating"]["cost_cutoff_month"] is None
        for key in ("operating_cost", "gross_profit", "gross_margin"):
            assert empty["kpis"][key]["actual"] is None and empty["kpis"][key]["status"] == "no_data", "no costs: not entered, not zero"

        assert cost(client, "2026-01", laborCost="30000").status_code == 200
        assert cost(client, "2026-03", laborCost="40000", otherCost="10000").status_code == 200
        result = review(client)
        operating = result["operating"]
        assert (operating["cost_cutoff_month"], operating["missing_cost_months"]) == ("2026-03", ["2026-02"])
        assert operating["revenue_after_tax_to_cutoff"] == "100000.00", "May's receipt is after the cost cut-off"
        assert (operating["operating_cost"], operating["gross_profit"], operating["gross_margin_percent"]) == ("80000.00", "20000.00", "20.00")
        assert result["kpis"]["gross_profit"]["as_of"] == "2026-03"
        assert result["kpis"]["revenue_after_tax"]["actual"] == "150000.00", "revenue itself runs to the review month"


def test_plan_targets_are_derived(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert apply(client, "setAnnualOperatingPlan", {"policy": "default", "year": 2026, "collectionTarget": "1060000",
                                                        "operatingCostTarget": "600000", "newContractTarget": "3000000"}).status_code == 200
        plan = client.get("/api/v1/ontology/objects/AnnualOperatingPlan/2026-实施交付部").json()["data"]["properties"]
        assert (plan["revenueTargetAfterTax"], plan["grossProfitTarget"], plan["grossMarginTargetPercent"]) == ("1000000.00", "400000.00", "40.00")
        assert apply(client, "setAnnualOperatingPlan", {"policy": "default", "year": 2027, "collectionTarget": "1060000"}).status_code == 200
        only_revenue = client.get("/api/v1/ontology/objects/AnnualOperatingPlan/2027-实施交付部").json()["data"]["properties"]
        assert only_revenue["revenueTargetAfterTax"] == "1000000.00" and only_revenue["grossProfitTarget"] is None
        schema = {t["apiName"]: t for t in client.get("/api/v1/ontology/schema").json()["data"]["objectTypes"]}
        labels = {p["apiName"]: p["displayName"] for p in schema["AnnualOperatingPlan"]["properties"]}
        assert labels["collectionTarget"] == "经营收入目标（税前）" and labels["newContractTarget"] == "合同额目标"
        assert {"capacityPersonDaysTarget", "headcountTarget", "utilizationTargetPercent"} <= set(labels), "resource targets stay (D29)"


def test_annual_plan_tracking_statuses():
    tolerance = Decimal("5")
    over = plan_completion(Decimal("700000"), Decimal("1200000"), 6, tolerance, lower_is_better=True)
    assert (over["completion_percent"], over["time_progress_percent"], over["status"]) == (58.33, 50.0, "over_budget")
    assert plan_completion(Decimal("550000"), Decimal("1200000"), 6, tolerance, lower_is_better=True)["status"] == "on_track"
    assert plan_completion(Decimal("35"), Decimal("40"), 10, tolerance, cumulative=False)["status"] == "behind"
    assert plan_completion(Decimal("42"), Decimal("40"), 10, tolerance, cumulative=False)["status"] == "on_track"
