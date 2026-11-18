"""CRM receipt changes as operating revenue (requirements baseline D68, ontology 8.11.0, user 2026-11-18).

XToolsCRM gives each contract's cumulative 回款金额 but no receipt dates. Each contract upload records how it changed:
an opening estimate for a contract signed this year (counted in its signing month), a baseline for others, then each
change on the upload day (negative when CRM went down). Projects with confirmed 回款节点 keep using those (D22).
"""
import base64
import json
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app

from test_annual_plan_metrics import receive
from test_api import settings


def upload(client, rows):
    content = base64.b64encode(json.dumps(rows, ensure_ascii=False).encode("utf-8")).decode()
    response = client.post("/api/v1/crm-imports", json={"data_type": "contract_order", "filename": "合同订单列表.json",
                                                        "content_base64": content})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def contract(code, signed, received, internal_id):
    return {"ID": internal_id, "主题": f"{code}-测试合同", "所有者": "Sales A", "签约时间": signed, "总金额": "500000", "回款金额": received}


def collections(client, today):
    response = client.get("/api/v1/department/review", params={"year": today.year, "month": today.isoformat()[:7]})
    assert response.status_code == 200, response.text
    kpi = response.json()["data"]["kpis"]["collections"]
    return Decimal(kpi["actual"] or "0"), {k: Decimal(v) for k, v in kpi["sources"].items()}


def test_crm_receipt_changes_become_revenue(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        today = client.app.state.project_repository.today()
        this_year, last_year = f"{today.year}-01-15", f"{today.year - 1}-06-30"
        before, _ = collections(client, today)

        first = upload(client, [contract("RCP101", this_year, "100000", "9001"), contract("RCP102", last_year, "50000", "9002")])
        assert first["receipt_changes"]["opening_estimates"] == 1 and first["receipt_changes"]["baselines"] == 1
        actual, sources = collections(client, today)
        assert actual - before == Decimal("100000"), "a contract signed this year counts its receipts as an opening estimate"
        assert sources["opening_estimate"] == Decimal("100000")

        second = upload(client, [contract("RCP101", this_year, "130000", "9001"), contract("RCP102", last_year, "40000", "9002")])
        assert second["receipt_changes"]["changes"] == 2 and second["receipt_changes"]["change_amount"] == "20000.00"
        actual, sources = collections(client, today)
        assert actual - before == Decimal("120000"), "+30,000 and −10,000 on the upload day"
        assert sources["crm_changes"] == Decimal("20000")

        again = upload(client, [contract("RCP101", this_year, "130000", "9001"), contract("RCP102", last_year, "40000", "9002")])
        assert again["receipt_changes"]["changes"] == 0, "an unchanged upload records nothing"

        changes = client.get("/api/v1/ontology/objects/CrmReceiptChange", params={"pageSize": 50}).json()["data"]
        kinds = sorted((c["properties"]["projectNumber"], c["properties"]["kind"], c["properties"]["changeAmount"]) for c in changes)
        assert kinds == [("RCP101", "change", "30000.00"), ("RCP101", "opening_estimate", "100000.00"),
                         ("RCP102", "baseline", "50000.00"), ("RCP102", "change", "-10000.00")]


def test_confirmed_payment_nodes_win_over_crm_changes(tmp_path):
    """A project with confirmed 回款节点 uses them; its CRM changes are not counted again."""
    with TestClient(create_app(settings(tmp_path))) as client:
        today = client.app.state.project_repository.today()
        receive(client, "首期款", "106000", f"{today.year}-01-10")
        before, _ = collections(client, today)
        upload(client, [contract("TPL149", f"{today.year}-01-05", "300000", "9101")])
        actual, sources = collections(client, today)
        assert actual == before and sources["opening_estimate"] == 0, "TPL149 has a confirmed 回款节点"
