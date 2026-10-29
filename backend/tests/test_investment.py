import base64
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.investment import InvestmentService, build_cost_objects
from app.main import create_app

from test_api import settings


def entry(external_id, code, work_date, hours="8.00", employee="E001"):
    return {
        "external_id": external_id, "project_number": code, "employee_id": employee,
        "employee_name": {"E001": "Zhang San", "E002": "Li Si"}[employee], "work_date": work_date,
        "hours": hours, "approved_hours": hours, "approval_status": "approved",
        "work_content": "投入", "source_updated_at": "2026-07-01T00:00:00+08:00",
    }


ENTRIES = [
    entry("I1", "TPL149", "2026-05-20"),                 # before contract 2026-06-16: cost to win
    entry("I2", "TPL149", "2026-06-16"),                 # on signing day: delivery
    entry("I3", "TPL149", "2026-07-02", employee="E002"),
    entry("I4", "WDR101", "2026-04-01"),                 # open opportunity, 20% win probability
    entry("I5", "WDR101", "2026-04-02"),
    entry("I6", "LOS300", "2026-03-01"),                 # CRM status 失败 -> sunk
    entry("I7", "NEW200", "2026-03-05", hours="4.00"),   # not in CRM -> unregistered
    entry("I8", "TSG999", "2026-03-06"),                 # internal
]


def import_lost_opportunity(client):
    csv = "主题,所有者,状态,可能性\nLOS300-丢单测试项目,张三,失败,30\n"
    response = client.post("/api/v1/crm-imports", json={
        "data_type": "sales_opportunity", "filename": "lost.csv",
        "content_base64": base64.b64encode(csv.encode("utf-8")).decode(),
    })
    assert response.status_code == 200, response.text


def setup(client):
    import_lost_opportunity(client)
    assert client.post("/api/v1/time-entries/import", json=ENTRIES).status_code == 200


def test_cost_objects_classify_effort_by_outcome_and_signing_date(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        setup(client)
        get = lambda code: client.get(f"/api/v1/investment/cost-objects/{code}").json()["data"]

        won = get("TPL149")
        assert (won["registration"], won["outcome"], won["conversion_date"]) == ("contracted", "won", "2026-06-16")
        assert won["presales_converted_person_days"] == "1.00"
        assert won["delivery_person_days"] == "2.00"
        assert won["cost_to_win_share"] == 33.3

        open_opportunity = get("WDR101")
        assert (open_opportunity["outcome"], open_opportunity["at_risk_person_days"]) == ("open", "2.00")
        assert open_opportunity["expected_sunk_person_days"] == "1.60"
        assert open_opportunity["probability_source"] == "CRM 赢率"
        assert open_opportunity["stalled"] is True, "no effort for longer than the 90-day threshold"

        lost = get("LOS300")
        assert (lost["outcome"], lost["outcome_source"], lost["outcome_reason"]) == ("lost", "CRM 状态", "失败")
        assert lost["sunk_person_days"] == "1.00"

        unregistered = get("NEW200")
        assert (unregistered["registration"], unregistered["unregistered_person_days"]) == ("unregistered", "0.50")
        assert get("TSG999")["internal_person_days"] == "1.00"
        assert client.get("/api/v1/investment/cost-objects/NOPE").status_code == 404


def test_portfolio_analysis_ratios_aging_and_period(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        setup(client)
        analysis = client.get("/api/v1/investment/analysis").json()["data"]
        totals = analysis["totals"]
        assert totals["total_person_days"] == "7.50"
        assert (totals["delivery_person_days"], totals["presales_converted_person_days"]) == ("2.00", "1.00")
        assert (totals["at_risk_person_days"], totals["sunk_person_days"]) == ("2.00", "1.00")
        assert (totals["internal_person_days"], totals["unregistered_person_days"]) == ("1.00", "0.50")
        ratios = analysis["ratios"]
        assert ratios["won_count"] == 2 and ratios["lost_count"] == 1 and ratios["open_count"] == 1
        assert ratios["win_rate_by_count"] == 66.7
        assert ratios["sunk_rate"] == 50.0
        assert ratios["average_cost_to_win_person_days"] == "0.50"
        assert ratios["effective_investment_share"] == 40.0
        assert sum(item["count"] for item in analysis["aging"]) == 1
        assert [item["code"] for item in analysis["unregistered"]] == ["NEW200"]
        assert analysis["sunk_reasons"] == [{"reason": "失败", "count": 1, "person_days": "1.00"}]
        assert analysis["cost_enabled"] is False and totals["total_cost"] is None
        assert {row["month"] for row in analysis["monthly"]} == {"2026-03", "2026-04", "2026-05", "2026-06", "2026-07"}

        march = client.get("/api/v1/investment/analysis", params={"start_month": "2026-03", "end_month": "2026-03"}).json()["data"]
        assert march["totals"]["total_person_days"] == "2.50"
        assert client.get("/api/v1/investment/analysis", params={"start_month": "2026-05", "end_month": "2026-03"}).status_code == 422


def test_outcome_actions_turn_at_risk_effort_into_sunk_cost(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        setup(client)

        def apply(action, parameters):
            return client.post(f"/api/v1/ontology/actions/{action}/apply", json={"parameters": parameters})

        assert apply("recordOpportunityOutcome", {"costObject": "TPL149", "outcome": "lost", "reason": "竞争失败"}).status_code == 422
        assert apply("recordOpportunityOutcome", {"costObject": "TSG999", "outcome": "lost", "reason": "竞争失败"}).status_code == 422
        too_early = apply("recordOpportunityOutcome", {"costObject": "WDR101", "outcome": "lost", "reason": "竞争失败", "outcomeDate": "2026-01-01"})
        assert too_early.status_code == 422 and "首次投入" in too_early.json()["detail"]
        assert apply("recordOpportunityOutcome", {"costObject": "WDR101", "outcome": "won", "reason": "竞争失败"}).status_code == 422
        assert apply("reopenOpportunity", {"costObject": "WDR101", "reason": "x"}).status_code == 422

        recorded = apply("recordOpportunityOutcome", {"costObject": "WDR101", "outcome": "abandoned", "reason": "预算不足", "outcomeDate": "2026-05-01", "notes": "客户冻结预算"})
        assert recorded.status_code == 200, recorded.text
        cost_object = recorded.json()["data"]["objects"][0]["properties"]
        assert (cost_object["opportunityOutcome"], cost_object["sunkPersonDays"], cost_object["atRiskPersonDays"]) == ("abandoned", "2.00", "0.00")
        project = client.get("/api/v1/ontology/objects/Project/WDR101").json()["data"]["properties"]
        assert project["opportunityOutcome"] == "abandoned" and project["sunkPersonDays"] == "2.00"
        analysis = client.get("/api/v1/investment/analysis").json()["data"]
        assert analysis["totals"]["sunk_person_days"] == "3.00"
        assert {r["reason"] for r in analysis["sunk_reasons"]} == {"预算不足", "失败"}

        unregistered = apply("recordOpportunityOutcome", {"costObject": "NEW200", "outcome": "lost", "reason": "其他"})
        assert unregistered.status_code == 200
        assert client.get("/api/v1/investment/cost-objects/NEW200").json()["data"]["sunk_person_days"] == "0.50"

        reopened = apply("reopenOpportunity", {"costObject": "WDR101", "reason": "客户重启采购"})
        assert reopened.status_code == 200
        assert client.get("/api/v1/investment/cost-objects/WDR101").json()["data"]["outcome"] == "open"
        log = client.get("/api/v1/ontology/action-log", params={"objectType": "CostObject", "primaryKey": "WDR101"}).json()["data"]
        assert [e["actionType"] for e in log][:2] == ["reopenOpportunity", "recordOpportunityOutcome"]


def test_cost_objects_in_ontology_links_and_time_entry_phases(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        setup(client)
        listing = client.get("/api/v1/ontology/objects/CostObject", params={"pageSize": 50}).json()
        assert {o["primaryKey"] for o in listing["data"]} >= {"TPL149", "WDR101", "LOS300", "NEW200", "TSG999"}
        assert listing["data"][0]["primaryKey"] == "TPL149", "default order is total effort descending"
        assert [o["primaryKey"] for o in client.get("/api/v1/ontology/objects/CostObject/TPL149/links/project").json()["data"]] == ["TPL149"]
        assert len(client.get("/api/v1/ontology/objects/CostObject/TPL149/links/timeEntries").json()["data"]) == 3
        assert {o["primaryKey"] for o in client.get("/api/v1/ontology/objects/CostObject/TPL149/links/team").json()["data"]} == {"E001", "E002"}
        phases = {o["primaryKey"]: o["properties"]["costPhase"] for o in client.get("/api/v1/ontology/objects/TimeEntry", params={"pageSize": 50}).json()["data"]}
        assert phases == {"I1": "presales_converted", "I2": "delivery", "I3": "delivery", "I4": "at_risk", "I5": "at_risk", "I6": "sunk", "I7": "unregistered", "I8": "internal"}
        sunk = client.post("/api/v1/ontology/objectSets/aggregate", json={
            "objectSet": {"type": "base", "objectType": "TimeEntry"},
            "aggregation": [{"type": "count"}], "groupBy": [{"type": "exact", "field": "costPhase"}]}).json()
        assert {row["group"]["costPhase"]: row["metrics"][0]["value"] for row in sunk["data"]}["at_risk"] == 2

        profile = client.post("/api/v1/ontology/functions/costObjectInvestment/execute", json={"parameters": {"costObject": "WDR101"}}).json()["data"]
        assert profile["outputType"] == "InvestmentProfile" and profile["result"]["at_risk_person_days"] == "2.00"
        portfolio = client.post("/api/v1/ontology/functions/portfolioInvestmentAnalysis/execute", json={"parameters": {}}).json()["data"]
        assert portfolio["result"]["totals"]["total_person_days"] == "7.50"


def test_investment_settings_and_cost_visibility(tmp_path):
    secure = replace(settings(tmp_path), auth_enabled=True)
    with TestClient(create_app(secure)) as client:
        admin = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Tsnt@2026"}).json()["data"]["token"]}
        import_lost = client.post("/api/v1/crm-imports", headers=admin, json={
            "data_type": "sales_opportunity", "filename": "lost.csv",
            "content_base64": base64.b64encode("主题,所有者,状态\nLOS300-丢单测试项目,张三,失败\n".encode()).decode()})
        assert import_lost.status_code == 200
        assert client.post("/api/v1/time-entries/import", headers=admin, json=ENTRIES).status_code == 200

        assert client.patch("/api/v1/investment/settings", headers=admin, json={"standard_person_day_cost": "-1"}).status_code == 422
        assert client.patch("/api/v1/investment/settings", headers=admin, json={"stall_days": 3}).status_code == 422
        assert client.patch("/api/v1/investment/settings", headers=admin, json={"internal_codes": "TSG999"}).status_code == 422
        updated = client.patch("/api/v1/investment/settings", headers=admin, json={"standard_person_day_cost": "1500", "internal_codes": ["tsg999", "NEW200"]}).json()["data"]
        assert updated["standard_person_day_cost"] == "1500.00" and updated["internal_codes"] == ["TSG999", "NEW200"]

        analysis = client.get("/api/v1/investment/analysis", headers=admin).json()["data"]
        assert analysis["cost_enabled"] is True
        assert analysis["totals"]["internal_person_days"] == "1.50", "NEW200 is now an internal code"
        assert analysis["totals"]["sunk_cost"] == "1500.00"

        client.post("/api/v1/users", headers=admin, json={"username": "analyst", "password": "analyst-pass-1", "permissions": ["investment"]})
        analyst = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "analyst", "password": "analyst-pass-1"}).json()["data"]["token"]}
        hidden = client.get("/api/v1/investment/analysis", headers=analyst).json()["data"]
        assert hidden["cost_enabled"] is False and hidden["totals"]["sunk_cost"] is None
        assert hidden["settings"]["standard_person_day_cost"] is None
        assert hidden["totals"]["sunk_person_days"] == "1.00", "person-days stay visible"
        assert client.patch("/api/v1/investment/settings", headers=analyst, json={"standard_person_day_cost": "1"}).status_code == 403
        assert client.patch("/api/v1/investment/settings", headers=analyst, json={"stall_days": 60}).status_code == 200
        cost_object = client.get("/api/v1/ontology/objects/CostObject/LOS300", headers=analyst).json()["data"]
        assert cost_object["properties"]["estimatedCost"] is None and "estimatedCost" in cost_object["redactedProperties"]
        assert client.post("/api/v1/ontology/actions/recordOpportunityOutcome/apply", headers=analyst, json={
            "parameters": {"costObject": "LOS300", "outcome": "lost", "reason": "竞争失败"}}).status_code == 200

        client.post("/api/v1/users", headers=admin, json={"username": "crmonly", "password": "crmonly-pass-1", "permissions": ["crm"]})
        crm_only = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "crmonly", "password": "crmonly-pass-1"}).json()["data"]["token"]}
        assert client.get("/api/v1/investment/analysis", headers=crm_only).status_code == 403


SETTINGS = {"standard_person_day_cost": None, "stall_days": 90, "lost_statuses": ["失败"], "internal_codes": ["TSG999"]}


def build(crm, entries, outcomes=None, **period):
    items = build_cost_objects(crm, entries, outcomes or {}, {}, SETTINGS, default_hours=Decimal("8"), today=date(2026, 10, 1), **period)
    return {item["code"]: item for item in items}


def crm(code, data_type, status=None, signed_at=None, probability=None):
    return {"project_number": code, "data_type": data_type, "subject": f"{code}-测试", "status": status,
            "signed_at": signed_at, "probability": probability}


def test_classification_precedence_and_data_flags():
    manual = {"outcome": "lost", "reason": "竞争失败", "outcome_date": "2026-04-01", "recorded_by": "pm"}
    items = build(
        [crm("CON1", "contract_order"),                                         # contract without signed date
         crm("OPP1", "sales_opportunity", status="成功", signed_at="2026-05-01"),  # won in CRM, no contract yet
         crm("OPP2", "sales_opportunity", status="成功"),
         crm("OPP3", "sales_opportunity", status="失败"),
         crm("tsg999", "sales_opportunity", status="进行中")],
        [entry("P1", "CON1", "2026-03-01"), entry("P2", "OPP1", "2026-04-30"), entry("P3", "OPP1", "2026-05-01"),
         entry("P4", "OPP2", "2026-04-01"), entry("P5", "OPP3", "2026-04-01"), entry("P6", "tsg999", "2026-04-01")],
        {"CON1": manual, "OPP3": manual},
    )
    contracted = items["CON1"]
    assert (contracted["outcome"], contracted["delivery_person_days"], contracted["sunk_person_days"]) == ("won", "1.00", "0.00")
    assert contracted["data_flags"] == ["合同缺少签订日期，投入全部计为履约成本", "已签约，忽略人工丢单结论"]

    crm_won = items["OPP1"]
    assert (crm_won["registration"], crm_won["outcome_source"]) == ("opportunity", "CRM 商机成功")
    assert (crm_won["conversion_date"], crm_won["conversion_date_source"]) == ("2026-05-01", "商机签单日期")
    assert (crm_won["presales_converted_person_days"], crm_won["delivery_person_days"]) == ("1.00", "1.00")
    assert items["OPP2"]["data_flags"] == ["商机已成功但尚无合同和签单日期，投入全部计为履约成本"]

    manual_over_crm = items["OPP3"]
    assert (manual_over_crm["outcome_source"], manual_over_crm["outcome_reason"]) == ("人工确认", "竞争失败")
    assert manual_over_crm["outcome_recorded_by"] == "pm"
    assert (items["tsg999"]["registration"], items["tsg999"]["internal_person_days"]) == ("internal", "1.00"), "internal codes match case-insensitively"


def test_expected_sunk_falls_back_to_historical_win_rate_and_period_filter():
    items = build(
        [crm("WON1", "contract_order", signed_at="2026-01-01"),
         crm("LOST1", "sales_opportunity", status="失败"),
         crm("OPEN1", "sales_opportunity", status="进行中"),
         crm("OPEN2", "sales_opportunity", status="进行中", probability=25)],
        [entry("Q1", "OPEN1", "2026-09-01"), entry("Q2", "OPEN1", "2026-09-20", hours="4.00"),
         entry("Q3", "OPEN2", "2026-09-01"), entry("Q4", "WON1", "2026-02-01")],
    )
    no_probability = items["OPEN1"]
    assert (no_probability["probability_source"], no_probability["expected_sunk_person_days"]) == ("历史赢单率", "0.75")
    assert "CRM 未填写赢率" in no_probability["data_flags"]
    assert (no_probability["presales_age_days"], no_probability["stalled"]) == (30, False)
    assert (items["OPEN2"]["probability_source"], items["OPEN2"]["expected_sunk_person_days"]) == ("CRM 赢率", "0.75")

    only_open = build([crm("OPEN1", "sales_opportunity", status="进行中")], [entry("R1", "OPEN1", "2026-09-01")])["OPEN1"]
    assert (only_open["probability_source"], only_open["expected_sunk_person_days"]) == (None, None), "no history, no estimate"
    september = build([], [entry("R1", "X1", "2026-08-31"), entry("R2", "X1", "2026-09-01")], start_month="2026-09", end_month="2026-09")["X1"]
    assert (september["entry_count"], september["total_person_days"], september["first_work_date"]) == (1, "1.00", "2026-08-31")


def test_service_validation_errors(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        state = client.app.state
        service = InvestmentService(state.database, state.project_repository, state.repository, "Asia/Shanghai")
        for bad in (True, "abc", "NaN", "Infinity", "0"):
            with pytest.raises(ValueError):
                service.update_settings({"standard_person_day_cost": bad})
        for bad in (True, 6, 731, "90"):
            with pytest.raises(ValueError):
                service.update_settings({"stall_days": bad})
        with pytest.raises(ValueError):
            service.update_settings({"lost_statuses": ["失败", 1]})
        assert service.update_settings({"standard_person_day_cost": ""})["standard_person_day_cost"] is None

        with pytest.raises(ValueError):
            service.record_outcome("X1", "won", "竞争失败", "2026-01-01", None, "pm")
        with pytest.raises(ValueError):
            service.record_outcome("X1", "lost", "不喜欢", "2026-01-01", None, "pm")
        with pytest.raises(ValueError):
            service.record_outcome("X1", "lost", "其他", "2026-13-01", None, "pm")
        with pytest.raises(KeyError):
            service.reopen("NOPE")
        with pytest.raises(ValueError):
            service.analysis(start_month="2026/01")

        single = service.analysis(start_month="2026-07")
        assert (single["period"]["start_month"], single["period"]["end_month"]) == ("2026-07", "2026-07")
