"""History of interventions and AI strategies (ontology 8.8.0, requirements baseline D61-D63, build package
docs/ontology-builds/2026-11-10-intervention-history.yaml).

Every intervention has a timeline (D61, D62): where it came from (an AI suggestion or a department strategy, who
submitted and approved it, the facts), when it was opened, moved and closed with its conclusion, and what happened to
its strategy. Closing needs a conclusion on every write path (D63). Strategies no longer in force stay listed on the
department page with their interventions (D61). Read from the action audit and the records; nothing new is stored.
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology.runtime import ActionValidationError

from test_ai_findings import ADMIN, HEAD, PM, PMO, act, budget_fact, objects, submit, today
from test_api import settings


@pytest.fixture()
def app(tmp_path):
    application = create_app(settings(tmp_path))
    with TestClient(application):
        application.state.ontology_service.apply_action(ADMIN, "configureProject", {
            "project": "WDR101", "contractType": "construction", "plannedPersonDays": 120, "projectManager": "刘浪"}, channel="business")
        yield application


def history(app, intervention, user=ADMIN):
    return app.state.ontology_service.execute_function(user, "interventionHistory", {"intervention": intervention})["result"]


def open_intervention(app, title="补充测试资源"):
    result = act(app, PMO, "openIntervention", project="WDR101", title=title, dueDate=(today(app) + timedelta(days=10)).isoformat(), notes="测试人手不足")
    return next(e["primaryKey"] for e in result["edits"] if e["objectType"] == "Intervention")


def test_closing_needs_a_conclusion_on_every_write_path(app):
    intervention = open_intervention(app)
    with pytest.raises(ActionValidationError, match="处理结论"):
        act(app, PMO, "updateInterventionStatus", intervention=intervention, status="closed")
    with pytest.raises(ActionValidationError, match="处理结论"):
        act(app, PMO, "updateInterventionStatus", intervention=intervention, status="closed", conclusion="   ")
    with pytest.raises(ValueError, match="处理结论"):
        app.state.project_repository.update_intervention_status("WDR101", intervention, "closed")
    act(app, PMO, "updateInterventionStatus", intervention=intervention, status="in_progress", notes="已协调外包")
    act(app, PMO, "updateInterventionStatus", intervention=intervention, status="closed", conclusion="外包两人已到岗")
    closed = objects(app, "Intervention")[intervention]
    assert (closed["status"], closed["conclusion"], closed["notes"]) == ("closed", "外包两人已到岗", "已协调外包"), \
        "the conclusion is its own field; the notes stay"


def test_a_manual_intervention_has_a_timeline(app):
    intervention = open_intervention(app)
    act(app, PMO, "updateInterventionStatus", intervention=intervention, status="in_progress", notes="已协调外包")
    act(app, PM, "updateInterventionStatus", intervention=intervention, status="closed", conclusion="外包两人已到岗")
    result = history(app, intervention)
    assert result["origin"] is None and result["conclusion"] == "外包两人已到岗"
    assert [(e["title"], e["actor"]) for e in result["events"]] == [
        ("发起干预", "pmo.a"), ("状态改为「处理中」", "pmo.a"), ("状态改为「已关闭」", "pm.a")]
    assert result["events"][1]["detail"] == "备注：已协调外包" and result["events"][2]["detail"] == "处理结论：外包两人已到岗"
    assert [e["at"] for e in result["events"]] == sorted(e["at"] for e in result["events"])


def test_an_intervention_from_a_strategy_traces_back_to_it(app):
    proposal = submit(app, kind="strategyNote", project=None, category="push_acceptance", title="集中推动验收回款", basis=[budget_fact()])
    approved = act(app, HEAD, "approveAiProposal", proposal=proposal, projects=["WDR101"], dueDate=(today(app) + timedelta(days=7)).isoformat())
    intervention = next(e["primaryKey"] for e in approved["edits"] if e["objectType"] == "Intervention")
    note = next(e["primaryKey"] for e in approved["edits"] if e["objectType"] == "AiStrategyNote")
    act(app, HEAD, "closeAiStrategyNote", note=note, reason="本月已安排")
    result = history(app, intervention, PM)
    assert (result["origin"]["type"], result["origin"]["title"], result["origin"]["statusLabel"]) == ("strategy", "集中推动验收回款", "已关闭")
    assert result["origin"]["basis"] == ["项目 WDR101 的预算人天 ≥ 100（当前 120.00）"]
    titles = [e["title"] for e in result["events"]]
    assert titles[:3] == ["提交 AI 策略「集中推动验收回款」", "采纳部门策略", "部门策略分解到本项目"] and titles[-1] == "所属策略已关闭"
    assert result["events"][0]["actor"] == "pmo.a" and result["events"][1]["actor"] == "head.a"
    assert result["events"][-1]["detail"] == "本月已安排"


def test_an_intervention_from_a_suggestion_shows_when_its_facts_stopped_holding(app):
    proposal = submit(app, kind="intervention", title="追加预算", dueDate=(today(app) + timedelta(days=7)).isoformat())
    approved = act(app, PMO, "approveAiProposal", proposal=proposal)
    intervention = next(e["primaryKey"] for e in approved["edits"] if e["objectType"] == "Intervention")
    act(app, ADMIN, "configureProject", project="WDR101", plannedPersonDays=80)
    app.state.ai_findings.refresh()
    result = history(app, intervention)
    assert result["origin"]["type"] == "suggestion"
    assert [e["title"] for e in result["events"]] == ["提交 AI 建议「追加预算」", "批准 AI 建议", "批准 AI 建议，新建干预", "复核：依据已不成立"]
    assert "当前 80.00" in result["events"][-1]["detail"] and result["events"][-1]["actor"] == "系统"


def test_history_follows_the_scope_and_covers_records_from_before_the_audit(app):
    other = app.state.project_repository.create_intervention("ZMJ113", {"title": "旧干预", "due_date": "2026-12-31"})
    with pytest.raises(Exception):
        history(app, other["intervention_id"], PM)
    result = history(app, other["intervention_id"])
    assert [e["title"] for e in result["events"]] == ["已登记"], "written without an action: only when it was registered"


def test_strategies_no_longer_in_force_are_kept_with_their_outcomes(app):
    due = (today(app) + timedelta(days=7)).isoformat()
    proposal = submit(app, kind="strategyNote", project=None, category="push_acceptance", title="集中推动验收回款", basis=[budget_fact()])
    approved = act(app, HEAD, "approveAiProposal", proposal=proposal, projects=["WDR101"], dueDate=due)
    intervention = next(e["primaryKey"] for e in approved["edits"] if e["objectType"] == "Intervention")
    note = next(e["primaryKey"] for e in approved["edits"] if e["objectType"] == "AiStrategyNote")
    act(app, PM, "updateInterventionStatus", intervention=intervention, status="closed", conclusion="客户已签验收单")
    review = app.state.ontology_service.execute_function(HEAD, "departmentOperatingReview", {})["result"]
    assert review["aiStrategyHistory"] == [] and review["aiStrategyNotes"][0]["followUps"][0]["conclusion"] == "客户已签验收单"
    act(app, HEAD, "closeAiStrategyNote", note=note, reason="已落实")
    [past] = app.state.ontology_service.execute_function(HEAD, "departmentOperatingReview", {})["result"]["aiStrategyHistory"]
    assert (past["title"], past["statusLabel"], past["closedReason"], past["closedBy"]) == ("集中推动验收回款", "已关闭", "已落实", "head.a")
    assert [(f["projectNumber"], f["statusLabel"], f["conclusion"]) for f in past["followUps"]] == [("WDR101", "已关闭", "客户已签验收单")]
    assert past["basis"] == ["项目 WDR101 的预算人天 ≥ 100（当前 120.00）"]
