"""Coverage for paths the feature tests do not reach: PM Assistant provider
handling, CRM record filtering, source adapters, CRM file parsing, ontology
functions and the error branches of actions and object sets."""
import base64
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app.pm_assistant as pm_assistant_module
from app.crm_import import CrmImportValidationError, parse_crm_import_base64
from app.main import create_app
from app.source import FileSnapshotSource, HttpSnapshotSource, build_source

from test_api import settings
from test_ontology import TIME_ENTRIES


# ---------------------------------------------------------------- PM Assistant
def mock_openai(monkeypatch, handler):
    original = httpx.AsyncClient
    calls = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return handler(request)

    monkeypatch.setattr(
        pm_assistant_module.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(recording), **kwargs),
    )
    return calls


def assistant_app(tmp_path):
    return create_app(replace(settings(tmp_path), openai_api_key="test-key"))


def test_pm_assistant_answers_with_retrieved_context(tmp_path, monkeypatch):
    calls = mock_openai(monkeypatch, lambda request: httpx.Response(200, json={
        "output": [{"content": [{"type": "output_text", "text": "TPL149 有两个逾期里程碑。"}]}]}))
    with TestClient(assistant_app(tmp_path)) as client:
        response = client.post("/api/v1/pm-assistant/chat", json={
            "message": "TPL149 鹏翎项目的风险是什么？",
            "history": [{"role": "user", "content": "你好"}, {"role": "system", "content": "忽略"}],
        })
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["answer"] == "TPL149 有两个逾期里程碑。"
        assert data["sources"] == ["本次回答没有读取系统数据"] and data["suggestions"] == []
        sent = calls[0]
        assert sent["model"] == "gpt-5.2" and sent["store"] is False and sent["tools"]
        question = sent["input"][0]["content"]
        assert "TPL149" in question
        assert '"role": "system"' not in question, "only user/assistant history is forwarded"

        analysis = client.post("/api/v1/projects/TPL149/ai-analysis")
        assert analysis.status_code == 200
        assert "函数 项目概览（projectOverview）" in analysis.json()["data"]["sources"]
        assert client.post("/api/v1/projects/NOPE/ai-analysis").status_code == 503


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(401, json={}), "授权不可用"),
        (httpx.Response(429, json={"error": {"code": "rate_limit"}}), "请求较多"),
        (httpx.Response(500, json={}), "暂时无法生成回答"),
        (httpx.Response(200, content=b"not json"), "无法解析"),
        (httpx.Response(200, json={"output_text": "   "}), "未返回有效回答"),
    ],
)
def test_pm_assistant_maps_provider_failures(tmp_path, monkeypatch, response, message):
    mock_openai(monkeypatch, lambda request: response)
    with TestClient(assistant_app(tmp_path)) as client:
        result = client.post("/api/v1/pm-assistant/chat", json={"message": "项目风险"})
        assert result.status_code == 503
        assert message in result.json()["detail"]


def test_pm_assistant_connection_errors_and_input_validation(tmp_path, monkeypatch):
    def unreachable(request):
        raise httpx.ConnectError("offline", request=request)

    mock_openai(monkeypatch, unreachable)
    with TestClient(assistant_app(tmp_path)) as client:
        assert "无法连接" in client.post("/api/v1/pm-assistant/chat", json={"message": "风险"}).json()["detail"]
        assert client.post("/api/v1/pm-assistant/chat", json={"message": "  "}).status_code == 422
        assert client.post("/api/v1/pm-assistant/chat", json={"message": "x" * 2001}).status_code == 422
        assert client.post("/api/v1/pm-assistant/chat", json={"message": "hi", "history": "x"}).status_code == 422


# ------------------------------------------------------------- CRM records
def test_crm_record_filters_sorting_and_links(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        everything = client.get("/api/v1/records").json()
        total = everything["meta"]["total_records"]
        assert total >= 3
        record_id = everything["data"][0]["record_id"]
        assert client.get(f"/api/v1/records/{record_id}").json()["data"]["record_id"] == record_id
        assert client.get("/api/v1/records/missing").status_code == 404

        contracts = client.get("/api/v1/records", params={"data_type": "contract_order"}).json()["data"]
        assert contracts and all(r["data_type"] == "contract_order" for r in contracts)
        # CRM status is kept verbatim; filtering by a source status returns exactly those records.
        statuses = {r["status"] for r in everything["data"] if r["status"]}
        assert statuses
        by_status = {s: client.get("/api/v1/records", params={"status": s}).json()["data"] for s in statuses}
        assert all(rows and all(r["status"] == s for r in rows) for s, rows in by_status.items())
        assert sum(len(rows) for rows in by_status.values()) == sum(1 for r in everything["data"] if r["status"])

        searched = client.get("/api/v1/records", params={"q": "TPL149"}).json()["data"]
        assert searched and all("TPL149" in (r["project_number"] or "") + r["subject"] for r in searched)
        owner = everything["data"][0]["owner"]
        assert all(r["owner"] == owner for r in client.get("/api/v1/records", params={"owner": owner}).json()["data"])
        dated = client.get("/api/v1/records", params={"signed_from": "2000-01-01", "signed_to": "2999-12-31"}).json()
        assert dated["meta"]["total_records"] <= total

        descending = client.get("/api/v1/records", params={"sort": "-project_number"}).json()["data"]
        numbers = [r["project_number"] or "" for r in descending]
        assert numbers == sorted(numbers, reverse=True)

        first_page = client.get("/api/v1/records", params={"page_size": 1}).json()
        assert first_page["links"]["next"] and first_page["links"]["previous"] is None
        # Pagination is cursor based: follow the next link to the second page.
        second_page = client.get(first_page["links"]["next"]).json()
        assert second_page["data"] and second_page["data"][0]["record_id"] != first_page["data"][0]["record_id"]
        stale = client.get("/api/v1/records", params={"updated_since": "2999-01-01"}).json()
        assert stale["data"] == [] and stale["meta"]["total_records"] == 0
        assert client.get("/api/v1/records", params={"sort": "nonsense"}).status_code == 422
        assert client.get("/api/v1/projects/TPL149/records").json()["meta"]["total_records"] >= 1
        assert client.get("/api/v1/projects/NOPE/records").status_code == 404


def test_records_unavailable_without_snapshot(tmp_path):
    empty = replace(settings(tmp_path), auto_import_on_empty=False)
    with TestClient(create_app(empty)) as client:
        assert client.get("/api/v1/records").status_code == 503
        assert client.get("/api/v1/records/any").status_code == 404
        assert client.get("/api/v1/exports/latest", params={"format": "json"}).status_code == 503
        assert client.get("/api/v1/sync-status").json()["status"] == "never_run"


# ------------------------------------------------------------ source adapters
def test_snapshot_source_adapters(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"records": []}), encoding="utf-8")
    base = settings(tmp_path)
    assert isinstance(build_source(replace(base, source_file=snapshot)), FileSnapshotSource)
    with pytest.raises(ValueError, match="XTCRM_SOURCE_URL"):
        build_source(replace(base, source_kind="http"))
    with pytest.raises(ValueError, match="unsupported"):
        build_source(replace(base, source_kind="ftp"))

    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"records": [1]}'

    def fake_urlopen(request, timeout):
        seen["auth"] = request.headers.get("Authorization")
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.source.urllib.request.urlopen", fake_urlopen)
    source = build_source(replace(base, source_kind="http", source_url="https://crm.test/export", source_token="secret"))
    assert isinstance(source, HttpSnapshotSource)
    import asyncio

    assert asyncio.run(source.fetch()) == {"records": [1]}
    assert seen == {"auth": "Bearer secret", "timeout": 60}
    assert asyncio.run(FileSnapshotSource(snapshot).fetch()) == {"records": []}


# ------------------------------------------------------------ CRM file parsing
def encode(text: str | bytes) -> str:
    return base64.b64encode(text.encode("utf-8") if isinstance(text, str) else text).decode()


def test_crm_csv_and_json_parsing_normalizes_values():
    csv_text = "﻿主题,所有者,签约时间,可能性,总金额\nABC123-测试项目,张三,45000,0.6,1,234\n,,,,\n"
    records, summary = parse_crm_import_base64(content_base64=encode(csv_text), filename="a.csv", data_type="sales_opportunity")
    assert summary["parsed_rows"] == 1 and summary["unmatched_project_rows"] == 0
    row = records[0]
    assert row["项目编号"] == "ABC123"
    assert row["签约时间"] == "2023-03-15", "Excel serial dates become ISO dates"
    assert row["可能性"] == "60%"
    assert row["总金额"] == "1,234", "unquoted thousands separators are repaired"

    payload = json.dumps({"records": [{"主题": "无编号主题", "负责人": "李四", "签约日期": "2026/3/5"}]})
    records, summary = parse_crm_import_base64(
        content_base64="data:application/json;base64," + encode(payload), filename="b.json", data_type="contract_order")
    assert records[0]["签约时间"] == "2026-03-05"
    assert summary["unmatched_project_subjects"] == ["无编号主题"]


@pytest.mark.parametrize(
    ("filename", "content", "data_type", "message"),
    [
        ("x.txt", "a", "contract_order", "only .xlsx"),
        ("x.json", "{bad", "contract_order", "JSON file is invalid"),
        ("x.json", "[1, 2]", "contract_order", "array of data rows"),
        ("x.csv", "主题".encode("gbk"), "contract_order", "importable rows"),  # GBK is decoded; header only
        ("x.csv", "主题\nA", "invoice", "data_type must be"),
    ],
)
def test_crm_parsing_rejects_invalid_files(filename, content, data_type, message):
    with pytest.raises(CrmImportValidationError, match=message):
        parse_crm_import_base64(content_base64=encode(content), filename=filename, data_type=data_type)
    with pytest.raises(CrmImportValidationError, match="base64"):
        parse_crm_import_base64(content_base64="!!!", filename="a.csv", data_type="contract_order")


# -------------------------------------------------------- ontology functions
def execute(client, name, parameters, headers=None):
    return client.post(f"/api/v1/ontology/functions/{name}/execute", headers=headers, json={"parameters": parameters})


def test_ontology_functions_cover_payment_health_people_and_portfolio(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.post("/api/v1/time-entries/import", json=TIME_ENTRIES).status_code == 200
        payment = execute(client, "projectPaymentStatus", {"project": "TPL149"}).json()["data"]["result"]
        assert payment["contractValue"] == "149900.00" and payment["collectionRate"] == 0.0
        assert payment["paymentPlan"]["total_nodes"] == 2

        health = execute(client, "projectHealthForPeriod", {"project": "TPL149", "startMonth": "2026-07"}).json()["data"]["result"]
        assert health["period"]["start_month"] == "2026-07" and health["health"]["project_number"] == "TPL149"
        assert execute(client, "projectHealthForPeriod", {"project": "TPL149", "startMonth": "July"}).status_code == 422

        people = execute(client, "employeeUtilization", {"employee": "E001", "month": "2026-07"}).json()["data"]["result"]
        assert people["employeeName"] == "Zhang San" and people["assessment"] is not None
        assert execute(client, "employeeUtilization", {"employee": "E999", "month": "2026-07"}).status_code == 422

        portfolio = execute(client, "portfolioSummary", {"riskLevel": "warning"}).json()["data"]["result"]
        assert portfolio["total_projects"] == 2 and portfolio["healthy_projects"] == 0
        assert sum(portfolio["by_contract_type"].values()) == 2

        budget = execute(client, "projectBudgetStatus", {"project": "ZMJ113"}).json()["data"]["result"]
        assert budget["status"] == "无上限（按确认工时结算）"
        assert execute(client, "unknownFunction", {}).status_code == 404
        assert execute(client, "portfolioSummary", {"riskLevel": "purple"}).status_code == 422


def test_risk_function_redacts_payment_amounts_without_finance_access(tmp_path):
    secure = replace(settings(tmp_path), auth_enabled=True)
    with TestClient(create_app(secure)) as client:
        admin = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Tsnt@2026"}).json()["data"]["token"]}
        client.post("/api/v1/users", headers=admin, json={
            "username": "healthonly", "password": "health-pass-1", "permissions": ["health"]})
        health = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "healthonly", "password": "health-pass-1"}).json()["data"]["token"]}
        admin_alerts = execute(client, "projectRiskAssessment", {"project": "TPL149"}, admin).json()["data"]["result"]["anomalyAlerts"]
        redacted = execute(client, "projectRiskAssessment", {"project": "TPL149"}, health).json()["data"]["result"]["anomalyAlerts"]
        admin_payment = [a["detail"] for a in admin_alerts if a["code"] == "PAYMENT_DUE"]
        hidden_payment = [a["detail"] for a in redacted if a["code"] == "PAYMENT_DUE"]
        assert admin_payment and all("元" in d for d in admin_payment)
        assert hidden_payment and all("已脱敏" in d for d in hidden_payment)


# ------------------------------------------------------- ontology error paths
def apply(client, action, parameters, validate_only=False):
    return client.post(f"/api/v1/ontology/actions/{action}/apply", json={"parameters": parameters, "validateOnly": validate_only})


@pytest.mark.parametrize(
    ("action", "parameters", "parameter"),
    [
        ("createMilestone", {"project": "TPL149", "name": "x", "plannedEnd": "2026-13-40"}, "plannedEnd"),
        ("createMilestone", {"project": "TPL149", "name": "x", "plannedEnd": "2026-12-01", "sequenceNo": "first"}, "sequenceNo"),
        ("createMilestone", {"project": "TPL149", "name": "x", "plannedEnd": "2026-12-01", "isPaymentNode": "yes"}, "isPaymentNode"),
        ("createMilestone", {"project": "TPL149", "name": "x", "plannedEnd": "2026-12-01", "paymentAmount": "lots"}, "paymentAmount"),
        ("createMilestone", {"project": "TPL149", "name": 5, "plannedEnd": "2026-12-01"}, "name"),
        ("createMilestone", {"project": "TPL149", "plannedEnd": "2026-12-01"}, "name"),
        ("createMilestone", {"project": "NOPE", "name": "x", "plannedEnd": "2026-12-01"}, "project"),
        ("createMilestone", {"project": "TPL149", "name": "x", "plannedEnd": "2026-12-01", "colour": "red"}, "colour"),
    ],
)
def test_action_parameter_validation(tmp_path, action, parameters, parameter):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = apply(client, action, parameters)
        assert response.status_code == 422
        assert parameter in {e["parameter"] for e in response.json()["errors"]["parameterErrors"]}
        dry = apply(client, action, parameters, validate_only=True).json()["data"]
        assert dry["valid"] is False


def test_ontology_not_found_and_bad_requests(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert apply(client, "launchRocket", {}).status_code == 404
        assert client.post("/api/v1/ontology/actions/configureProject/apply", json={
            "parameters": {"project": "TPL149"}, "validateOnly": "yes"}).status_code == 422
        assert client.post("/api/v1/ontology/actions/configureProject/apply", json={"parameters": []}).status_code == 422
        assert client.get("/api/v1/ontology/objects/Project/NOPE").status_code == 404
        assert client.get("/api/v1/ontology/objects/Project/NOPE/actions").status_code == 404
        assert client.get("/api/v1/ontology/objects/Project/NOPE/links/team").status_code == 404
        assert client.get("/api/v1/ontology/objects/Project/TPL149/links/friends").status_code == 404
        assert client.get("/api/v1/ontology/objects/Project", params={"where.nope": "x"}).status_code == 422
        assert client.get("/api/v1/ontology/objects/Project", params={"orderBy": "-contractValue"}).json()["data"][0]["primaryKey"] == "TPL149"
        log = client.get("/api/v1/ontology/action-log", params={"objectType": "Project"}).json()
        assert log["meta"]["page"] == 1

        # Ontology 6.0.0 retired createPaymentNode; existing nodes (seeded through the base program) stay maintainable.
        repository = client.app.state.project_repository
        node = repository.create_payment_node("TPL149", {"name": "尾款", "planned_date": "2026-12-01", "planned_amount": "1000"})["payment_node_id"]
        assert apply(client, "createPaymentNode", {"project": "TPL149", "name": "尾款", "plannedDate": "2026-12-01", "plannedAmount": "1"}).status_code == 404
        assert apply(client, "editPaymentNode", {"paymentNode": node, "plannedAmount": "-5"}).status_code == 422
        assert apply(client, "editPaymentNode", {"paymentNode": node, "plannedAmount": "1500", "notes": None}).status_code == 200
        assert apply(client, "confirmPaymentReceived", {"paymentNode": node, "actualAmount": "1500"}).status_code == 200
        assert apply(client, "confirmPaymentReceived", {"paymentNode": node}).status_code == 422
        assert apply(client, "deletePaymentNode", {"paymentNode": node}).status_code == 422

        free = repository.create_payment_node("TPL149", {"name": "预付款", "planned_date": "2026-11-01", "planned_amount": "10"})
        assert apply(client, "deletePaymentNode", {"paymentNode": free["payment_node_id"]}).status_code == 200

        milestone = apply(client, "createMilestone", {"project": "TPL149", "name": "试运行", "plannedEnd": "2026-12-10"}).json()["data"]
        milestone_id = milestone["edits"][0]["primaryKey"]
        # Ontology 7.0.0: a milestone gets its payment node only by linking a payment term.
        legacy = apply(client, "editMilestone", {"milestone": milestone_id, "isPaymentNode": True, "paymentDate": "2026-12-20", "paymentAmount": "800"})
        assert legacy.status_code == 422 and "isPaymentNode" in legacy.text
        term = client.post("/api/v1/projects/TPL149/payment-terms", json={"name": "试运行款", "planned_amount": "800"}).json()["data"]
        converted = apply(client, "editMilestone", {"milestone": milestone_id, "paymentTermId": term["payment_term_id"]}).json()["data"]
        assert [e["operation"] for e in converted["edits"]] == ["update", "create"]
        reverted = apply(client, "editMilestone", {"milestone": milestone_id, "paymentTermId": None}).json()["data"]
        assert [e["operation"] for e in reverted["edits"]] == ["update", "delete"]
        assert apply(client, "deleteMilestone", {"milestone": milestone_id}).status_code == 200


OBJECT_SET_ERRORS = [
    ({"type": "filter", "objectSet": {"type": "base", "objectType": "Project"}, "where": {"type": "isNull", "field": "notes", "value": "yes"}}, "布尔值"),
    ({"type": "filter", "objectSet": {"type": "base", "objectType": "Project"}, "where": {"type": "in", "field": "notes", "value": "x"}}, "数组"),
    ({"type": "filter", "objectSet": {"type": "base", "objectType": "Project"}, "where": {"type": "gt", "field": "maintenanceComplete", "value": True}}, "不支持大小比较"),
    ({"type": "filter", "objectSet": {"type": "base", "objectType": "Project"}, "where": {"type": "containsAnyTerm", "field": "subject", "value": "   "}}, "检索词"),
    ({"type": "filter", "objectSet": {"type": "base", "objectType": "Project"}, "where": {"type": "startsWith", "field": "subject", "value": 5}}, "非空字符串"),
    ({"type": "filter", "objectSet": {"type": "base", "objectType": "Project"}, "where": {"type": "gt", "field": "contractValue", "value": "lots"}}, "需要数值"),
    ({"type": "filter", "objectSet": {"type": "base", "objectType": "Project"}, "where": {"type": "eq", "field": "maintenanceComplete", "value": "no"}}, "布尔值"),
    ({"type": "filter", "objectSet": {"type": "base", "objectType": "Project"}, "where": {"type": "eq", "value": 1}}, "需要 field"),
    ({"type": "filter", "objectSet": {"type": "base", "objectType": "Project"}, "where": {"type": "regex", "field": "subject", "value": ".*"}}, "不支持的 where"),
    ({"type": "filter", "objectSet": {"type": "base", "objectType": "Project"}, "where": "x"}, "需要字段 where"),
    ({"type": "union", "objectSets": [{"type": "base", "objectType": "Project"}]}, "至少需要两个"),
    ({"type": "base"}, "需要字段 objectType"),
    ({"type": "base", "objectType": "Spaceship"}, "未知对象类型"),
    ("not-an-object", "带 type 字段"),
]


@pytest.mark.parametrize(("object_set", "message"), OBJECT_SET_ERRORS)
def test_object_set_validation_messages(tmp_path, object_set, message):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post("/api/v1/ontology/objectSets/loadObjects", json={"objectSet": object_set})
        assert response.status_code in {404, 422}
        assert message in response.json()["detail"]


def test_object_set_request_validation(tmp_path):
    projects = {"type": "base", "objectType": "Project"}
    nested = projects
    for _ in range(14):
        nested = {"type": "filter", "objectSet": nested, "where": {"type": "isNull", "field": "notes", "value": False}}
    with TestClient(create_app(settings(tmp_path))) as client:
        def load(body):
            return client.post("/api/v1/ontology/objectSets/loadObjects", json=body)

        def agg(aggregation, group_by=None):
            return client.post("/api/v1/ontology/objectSets/aggregate", json={
                "objectSet": projects, "aggregation": aggregation, "groupBy": group_by or []})

        assert "嵌套" in load({"objectSet": nested}).json()["detail"]
        assert load({"objectSet": projects, "pageSize": 0}).status_code == 422
        assert load({"objectSet": projects, "pageSize": "10"}).status_code == 422
        assert load({"objectSet": projects, "select": "subject"}).status_code == 422
        assert load({"objectSet": projects, "select": ["nope"]}).status_code == 422
        assert load({"objectSet": projects, "orderBy": {"fields": [{"field": "subject", "direction": "up"}]}}).status_code == 422
        assert load({"objectSet": projects, "orderBy": {"fields": ["subject"]}}).status_code == 422
        assert load({"objectSet": projects, "orderBy": {"fields": [{"field": "riskReasons"}]}}).status_code == 200

        assert agg([{"type": "median", "field": "contractValue"}]).status_code == 422
        assert agg(["count"]).status_code == 422
        assert agg([{"type": "min", "field": "maintenanceComplete"}]).status_code == 422
        no_projects = client.post("/api/v1/ontology/objectSets/aggregate", json={
            "objectSet": {"type": "static", "objectType": "Project", "objects": ["NOPE"]},
            "aggregation": [{"type": "min", "field": "plannedStart"}, {"type": "avg", "field": "contractValue"}]}).json()
        assert [m["value"] for m in no_projects["data"][0]["metrics"]] == [None, None]
        assert agg([{"type": "min", "field": "contractValue"}]).json()["data"][0]["metrics"][0]["value"] == "0.00"
        assert agg([{"type": "count"}], [{"type": "exact", "field": "riskLevel", "maxGroupCount": 0}]).status_code == 422
        assert agg([{"type": "count"}], [{"type": "fixedWidth", "field": "subject", "fixedWidth": 10}]).status_code == 422
        assert agg([{"type": "count"}], [{"type": "ranges", "field": "contractValue", "ranges": [5]}]).status_code == 422
        assert agg([{"type": "count"}], [{"type": "histogram", "field": "contractValue"}]).status_code == 422
        assert agg([{"type": "count"}], ["riskLevel"]).status_code == 422
        assert client.post("/api/v1/ontology/objectSets/aggregate", json={
            "objectSet": projects, "aggregation": [{"type": "count"}], "groupBy": "riskLevel"}).status_code == 422
        empty_groups = agg([{"type": "count"}], [{"type": "fixedWidth", "field": "plannedPersonDays", "fixedWidth": 10}]).json()
        assert empty_groups["excludedItems"] == 2


# ------------------------------------------------------ merged project table
def test_project_health_rows_carry_ledger_fields_filters_and_totals(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        portfolio = client.get("/api/v1/projects").json()
        health = client.get("/api/v1/project-health", params={"month": "2026-07"}).json()
        by_number = {p["project_number"]: p for p in portfolio["data"]}
        for row in health["data"]:
            project = by_number[row["project_number"]]
            for field in ("risk_level", "contract_type", "master_category", "customer", "anomaly_count"):
                assert row[field] == project[field], field
            assert row["payment_summary"] == project["payment_summary"]
            assert row["anomaly_alerts"] == project["anomaly_alerts"]
        summary = health["summary"]
        assert summary["contract_value"] == portfolio["summary"]["contract_value"]
        assert summary["received_amount"] == portfolio["summary"]["received_amount"]
        assert summary["risk_critical_projects"] == portfolio["summary"]["critical_projects"]
        assert summary["risk_warning_projects"] == portfolio["summary"]["warning_projects"]
        risk_order = {"critical": 0, "warning": 1, "healthy": 2}
        ranks = [risk_order[row["risk_level"]] for row in health["data"]]
        assert ranks == sorted(ranks), "projects needing attention come first"

        construction = client.get("/api/v1/project-health", params={"contract_type": "construction"}).json()
        assert [r["project_number"] for r in construction["data"]] == ["TPL149"]
        warning = client.get("/api/v1/project-health", params={"risk_level": "warning"}).json()
        assert {r["project_number"] for r in warning["data"]} == {"WDR101", "ZMJ113"}
        assert warning["summary"]["risk_warning_projects"] == 2
        assert client.get("/api/v1/project-health", params={"contract_type": "space"}).status_code == 422
        assert client.get("/api/v1/project-health", params={"risk_level": "purple"}).status_code == 422


def test_project_health_filters_by_master_category(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        rows = client.get("/api/v1/project-health").json()["data"]
        expected = {"项目": set(), "商机": set()}
        for row in rows:
            expected[row["master_category"]].add(row["project_number"])
        assert expected["项目"] and expected["商机"], "demo data contains both categories"
        for category, numbers in expected.items():
            filtered = client.get("/api/v1/project-health", params={"master_category": category}).json()
            assert {r["project_number"] for r in filtered["data"]} == numbers
            assert filtered["summary"]["total_projects"] == len(numbers)
        assert client.get("/api/v1/project-health", params={"master_category": "其他"}).status_code == 422
