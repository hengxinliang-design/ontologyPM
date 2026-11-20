"""项目管理 lists the most recent projects first (user 2026-11-20): 签约 / 预计签单 dates up to today newest first,
then expected signings still to come soonest first, then projects without a date."""
import base64
import json

from fastapi.testclient import TestClient

from app.main import create_app

from test_api import settings


def upload(client, data_type, rows):
    content = base64.b64encode(json.dumps(rows, ensure_ascii=False).encode("utf-8")).decode()
    response = client.post("/api/v1/crm-imports", json={"data_type": data_type, "filename": "列表.json", "content_base64": content})
    assert response.status_code == 200, response.text


def portfolio(client):
    response = client.post("/api/v1/ontology/functions/portfolioHealth/execute", json={"parameters": {"pageSize": 200}})
    assert response.status_code == 200, response.text
    return response.json()["data"]["result"]["data"]


def test_projects_are_listed_newest_signing_or_expected_date_first(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        upload(client, "contract_order", [
            {"ID": "8001", "主题": "ORD201-主合同", "所有者": "Sales A", "签约时间": "2026-03-01"},
            {"ID": "8002", "主题": "ORD201-补充协议", "所有者": "Sales A", "签约时间": "2026-11-15"},
            {"ID": "8003", "主题": "ORD202-合同", "所有者": "Sales A", "签约时间": "2026-10-01"},
        ])
        upload(client, "sales_opportunity", [
            {"ID": "9001", "机会主题": "ORD203-商机", "负责人": "Sales A", "预计签单日期": "2026-12-31", "预期金额": "100", "可能性": "0.3", "阶段": "项目立项50%"},
        ])
        today = client.app.state.project_repository.today().isoformat()
        upload(client, "sales_opportunity", [
            {"ID": "9002", "机会主题": "ORD204-商机", "负责人": "Sales A", "预计签单日期": "2099-01-31", "预期金额": "100", "可能性": "0.1", "阶段": "项目信息0%"},
        ])
        rows = portfolio(client)
        dates = [r["signing_date"] for r in rows]
        past = [d for d in dates if d and d <= today]
        future = [d for d in dates if d and d > today]
        undated = [d for d in dates if not d]
        assert dates == past + future + undated, "dates up to today, then future dates, then undated"
        assert past == sorted(past, reverse=True) and future == sorted(future), "past newest first, future soonest first"
        mine = {r["project_number"]: (r["signing_date"], r["signing_date_kind"]) for r in rows if r["project_number"].startswith("ORD")}
        assert mine["ORD201"] == ("2026-11-15", "签约"), "the latest contract (the supplementary agreement) decides"
        assert mine["ORD203"] == ("2026-12-31", "预计签单") and mine["ORD204"] == ("2099-01-31", "预计签单")
        order = [r["project_number"] for r in rows if r["project_number"].startswith("ORD")]
        assert order.index("ORD204") == len(order) - 1, "the furthest expected signing is not on top"
