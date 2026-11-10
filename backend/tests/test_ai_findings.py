"""AI suggestions written back after a person approves them (ontology 8.6.0, build package
docs/ontology-builds/2026-11-09-ai-writeback.yaml; requirements baseline D51-D55).

The model drafts candidates; a person submits one (D52) and someone whose role allows approves it (D51). Approved
interventions become ordinary interventions; AI risk flags show on the project without changing the risk level (D53);
AI strategy notes sit beside the rule signals. Every item cites facts the ontology checks; the hourly recheck closes
or expires what no longer holds, and one item is kept per subject + kind + facts (D54, D55). No model is called.
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology.runtime import ActionValidationError
from app.ontology.security import OntologyPermissionError

from test_api import settings

ADMIN = {"username": "admin.a", "is_admin": True, "permissions": []}
PMO = {"username": "pmo.a", "is_admin": False, "role": "pmo", "permissions": ["projects", "department"]}
HEAD = {"username": "head.a", "is_admin": False, "role": "department_head", "permissions": ["health", "department"]}
PM = {"username": "pm.a", "is_admin": False, "role": "project_manager", "person_name": "刘浪", "permissions": ["projects"]}


def budget_fact(project="WDR101", op=">=", value=100):
    return {"type": "fact", "objectType": "Project", "primaryKey": project, "property": "plannedPersonDays", "op": op, "value": value}


@pytest.fixture()
def app(tmp_path):
    application = create_app(settings(tmp_path))
    with TestClient(application):
        service = application.state.ontology_service
        service.apply_action(ADMIN, "configureProject", {"project": "WDR101", "contractType": "construction",
                                                         "plannedPersonDays": 120, "projectManager": "刘浪"}, channel="business")
        yield application


def act(app, user, action, **parameters):
    return app.state.ontology_service.apply_action(user, action, parameters, channel="business")


def check(app, user, action, **parameters):
    return app.state.ontology_service.apply_action(user, action, parameters, validate_only=True, channel="business")


def objects(app, object_type):
    ctx = app.state.ontology_service.context(app.state.ontology_service.SYSTEM_USER)
    return {str(o[ctx.ontology.object_type(object_type).primary_key]): o for o in ctx.objects(object_type)}


def submit(app, user=PMO, **fields):
    values = {"kind": "riskFlag", "project": "WDR101", "title": "预算可能不够", "basis": [budget_fact()], **fields}
    result = act(app, user, "submitAiProposal", **values)
    return next(e["primaryKey"] for e in result["edits"] if e["operation"] == "create")


def today(app):
    return app.state.project_repository.today()


def test_a_suggestion_needs_facts_that_hold_and_that_the_submitter_may_see(app):
    ok = check(app, PMO, "submitAiProposal", kind="riskFlag", project="WDR101", title="预算可能不够", basis=[budget_fact()])
    assert ok["valid"], ok
    wrong = check(app, PMO, "submitAiProposal", kind="riskFlag", project="WDR101", title="预算可能不够", basis=[budget_fact(value=500)])
    reasons = [c["reason"] for c in wrong["criteria"] if not c["passed"]]
    assert not wrong["valid"] and "依据不再成立" in reasons[0] and "当前 120.00" in reasons[0]
    for basis, words in [([], "至少引用一条"), ([{"type": "guess"}], "fact、alert、signal"),
                         ([{"type": "alert", "project": "WDR101", "code": "AI_RISK_FLAG"}], "AI 自己的标记"),
                         ([budget_fact(project="TPL149")], "关于项目 WDR101"),
                         ([{"type": "fact", "objectType": "Project", "primaryKey": "WDR101", "property": "nope", "op": "=", "value": 1}], "无法核对")]:
        result = check(app, PMO, "submitAiProposal", kind="riskFlag", project="WDR101", title="t", basis=basis)
        assert not result["valid"] and any(words in (c["reason"] or "") for c in result["criteria"]), (basis, result)
    no_date = check(app, PMO, "submitAiProposal", kind="intervention", project="WDR101", title="补资源", basis=[budget_fact()])
    assert not no_date["valid"] and any("截止日期" in (c["reason"] or "") for c in no_date["criteria"])
    # A project manager cannot cite a project outside their own (scope applies to the facts, D55).
    out_of_scope = check(app, PM, "submitAiProposal", kind="strategyNote", category="other", title="t", basis=[budget_fact(project="TPL149", op=">=", value=0)])
    assert not out_of_scope["valid"] and any("不在你的范围" in (c["reason"] or "") for c in out_of_scope["criteria"])


def test_approving_an_intervention_opens_an_ordinary_intervention_that_remembers_its_suggestion(app):
    due = (today(app) + timedelta(days=14)).isoformat()
    proposal = submit(app, kind="intervention", title="与客户确认追加预算", interventionType="resource", dueDate=due, detail="预算已用到 90%")
    pending = objects(app, "AiProposal")[proposal]
    assert (pending["status"], pending["submittedBy"], pending["parameters"]["dueDate"]) == ("pending", "pmo.a", due)
    assert pending["basis"][0]["text"].startswith("项目 WDR101 的预算人天 ≥ 100")
    project = objects(app, "Project")["WDR101"]
    assert project["pendingAiProposals"] == 1, "a pending suggestion is a pending item of the project (D52)"

    act(app, PMO, "approveAiProposal", proposal=proposal, note="同意")
    approved = objects(app, "AiProposal")[proposal]
    intervention = objects(app, "Intervention")[approved["resultId"]]
    assert (approved["status"], approved["decidedBy"], approved["decisionNote"]) == ("approved", "pmo.a", "同意"), "one person by role (D51)"
    assert (intervention["title"], intervention["interventionType"], intervention["dueDate"], intervention["owner"],
            intervention["sourceProposalId"]) == ("与客户确认追加预算", "resource", due, "刘浪", proposal)
    with pytest.raises(ActionValidationError, match="已批准"):
        act(app, PMO, "approveAiProposal", proposal=proposal)


def test_approver_roles(app):
    strategy = submit(app, kind="strategyNote", project=None, category="control_cost", title="控制 WDR101 的投入")
    pmo_try = check(app, PMO, "approveAiProposal", proposal=strategy)
    assert not pmo_try["valid"] and any("管理员或部门负责人" in (c["reason"] or "") for c in pmo_try["criteria"])
    assert check(app, HEAD, "approveAiProposal", proposal=strategy)["valid"], "the department head approves strategy notes"
    flag = submit(app)
    with pytest.raises(OntologyPermissionError):
        act(app, {**HEAD, "permissions": ["health"]}, "approveAiProposal", proposal=flag)
    assert check(app, PM, "approveAiProposal", proposal=flag)["valid"], "the project's own manager may approve project items"
    assert check(app, ADMIN, "approveAiProposal", proposal=strategy)["valid"]


def test_an_ai_risk_flag_is_a_pending_item_but_leaves_the_risk_level_to_the_rules(app):
    before = objects(app, "Project")["WDR101"]
    flag_proposal = submit(app)
    act(app, ADMIN, "approveAiProposal", proposal=flag_proposal)
    flag = next(f for f in objects(app, "AiRiskFlag").values() if f["proposalId"] == flag_proposal)
    valid_days = int(objects(app, "OperatingPolicy").popitem()[1]["aiFindingValidDays"])
    assert (flag["status"], flag["validUntil"]) == ("active", (today(app) + timedelta(days=valid_days)).isoformat())
    after = objects(app, "Project")["WDR101"]
    assert (after["activeAiRiskFlags"], after["pendingAiProposals"]) == (1, 0)
    assert after["riskLevel"] == before["riskLevel"], "D53: the risk level stays the rules' own"
    risk = app.state.ontology_service.execute_function(ADMIN, "projectRiskAssessment", {"project": "WDR101"})["result"]
    assert any(a["code"] == "AI_RISK_FLAG" and a["severity"] == "warning" and "预算可能不够" in a["title"] for a in risk["anomalyAlerts"])
    overview = app.state.ontology_service.execute_function(ADMIN, "aiSuggestionOverview", {"project": "WDR101"})["result"]
    assert [f["flagId"] for f in overview["riskFlags"]] == [flag["flagId"]] and overview["pending"] == []


def test_the_hourly_recheck_closes_what_no_longer_holds_and_expires_what_is_late(app):
    flag_proposal, late_proposal = submit(app), submit(app, title="另一个判断", basis=[budget_fact(op=">", value=50)])
    act(app, ADMIN, "approveAiProposal", proposal=flag_proposal)
    due = (today(app) + timedelta(days=7)).isoformat()
    intervention_proposal = submit(app, kind="intervention", title="追加预算", dueDate=due, basis=[budget_fact(value=110)])
    act(app, ADMIN, "approveAiProposal", proposal=intervention_proposal)

    refresh = app.state.ai_findings.refresh
    assert refresh() == {"checked": 3, "expired": 0, "closed": 0}, "nothing changes while the facts hold"
    act(app, ADMIN, "configureProject", project="WDR101", plannedPersonDays=80)
    assert refresh() == {"checked": 3, "expired": 0, "closed": 1}
    flag = next(f for f in objects(app, "AiRiskFlag").values() if f["proposalId"] == flag_proposal)
    assert (flag["status"], flag["closedBy"]) == ("closed", "system") and "依据不再成立" in flag["closedReason"] and "当前 80.00" in flag["closedReason"]
    source = objects(app, "AiProposal")[intervention_proposal]
    intervention = objects(app, "Intervention")[source["resultId"]]
    assert intervention["status"] == "open" and source["basisHolds"] is False and "当前 80.00" in source["basisNote"], \
        "an intervention is only marked; a person closes it (D54)"
    pending = objects(app, "AiProposal")[late_proposal]
    assert pending["status"] == "pending" and pending["basisHolds"], "80 > 50 still holds"

    assert refresh(today(app) + timedelta(days=8))["expired"] == 1
    late = objects(app, "AiProposal")[late_proposal]
    assert (late["status"], late["decidedBy"]) == ("expired", "system") and "超过待批天数" in late["decisionNote"]


def test_a_flag_expires_after_its_valid_days_unless_renewed(app):
    act(app, ADMIN, "updateOperatingPolicy", policy=objects(app, "OperatingPolicy").popitem()[0], aiFindingValidDays=10)
    act(app, ADMIN, "approveAiProposal", proposal=submit(app))
    flag = next(iter(objects(app, "AiRiskFlag").values()))
    assert flag["validUntil"] == (today(app) + timedelta(days=10)).isoformat()
    act(app, PMO, "renewAiRiskFlag", flag=flag["flagId"])
    assert app.state.ai_findings.refresh(today(app) + timedelta(days=9))["expired"] == 0
    assert app.state.ai_findings.refresh(today(app) + timedelta(days=11))["expired"] == 1
    expired = objects(app, "AiRiskFlag")[flag["flagId"]]
    assert expired["status"] == "expired" and "有效期到" in expired["closedReason"]
    with pytest.raises(ActionValidationError, match="只有有效的记录"):
        act(app, PMO, "closeAiRiskFlag", flag=flag["flagId"], reason="已处理")


def test_one_item_per_subject_kind_and_facts(app):
    first = submit(app)
    second = submit(app, title="同一判断的新说法", basis=[budget_fact(value=90)])
    proposals = objects(app, "AiProposal")
    assert proposals[first]["status"] == "superseded" and proposals[second]["status"] == "pending", "the value is not part of the key"
    other = submit(app, kind="intervention", title="追加预算", dueDate=(today(app) + timedelta(days=3)).isoformat())
    assert objects(app, "AiProposal")[second]["status"] == "pending" and objects(app, "AiProposal")[other]["status"] == "pending", \
        "another kind is another item"
    act(app, ADMIN, "approveAiProposal", proposal=second)
    act(app, ADMIN, "approveAiProposal", proposal=submit(app, title="再次确认"))
    flags = sorted(objects(app, "AiRiskFlag").values(), key=lambda f: f["approvedAt"])
    assert [f["status"] for f in flags] == ["superseded", "active"], "a newly approved flag replaces the active one"


def test_strategy_notes_sit_beside_the_rule_signals(app):
    note = submit(app, kind="strategyNote", project=None, category="win_contracts", title="争取 WDR101 二期", basis=[budget_fact()])
    review = app.state.ontology_service.execute_function(HEAD, "departmentOperatingReview", {})["result"]
    assert [p["proposalId"] for p in review["aiStrategyProposals"]] == [note] and review["canApproveStrategy"]
    act(app, HEAD, "approveAiProposal", proposal=note)
    review = app.state.ontology_service.execute_function(HEAD, "departmentOperatingReview", {})["result"]
    assert [(n["title"], n["categoryLabel"], n["approvedBy"]) for n in review["aiStrategyNotes"]] == [("争取 WDR101 二期", "争取新合同", "head.a")]
    assert review["aiStrategyProposals"] == [] and all(s["signal"] != "win_contracts" or s["evidence"] for s in review["signals"])
    note_id = review["aiStrategyNotes"][0]["noteId"]
    assert not check(app, PMO, "closeAiStrategyNote", note=note_id)["valid"]
    act(app, HEAD, "closeAiStrategyNote", note=note_id, reason="已安排")
    closed = objects(app, "AiStrategyNote")[note_id]
    assert (closed["status"], closed["closedBy"], closed["closedReason"]) == ("closed", "head.a", "已安排")


def test_the_day_counts_are_operating_policy_parameters(app):
    policy_id, policy = objects(app, "OperatingPolicy").popitem()
    assert (policy["aiProposalPendingDays"], policy["aiFindingValidDays"]) == (7, 30)
    with pytest.raises(ActionValidationError):
        act(app, ADMIN, "updateOperatingPolicy", policy=policy_id, aiProposalPendingDays=0)
    act(app, ADMIN, "updateOperatingPolicy", policy=policy_id, aiProposalPendingDays=3)
    proposal_id = submit(app)
    proposal = objects(app, "AiProposal")[proposal_id]
    assert proposal["expiresOn"] == (today(app) + timedelta(days=3)).isoformat()


def test_the_assistant_may_draft_a_suggestion_but_never_approve_one(app):
    from app.pm_assistant import AssistantSession

    session = AssistantSession(app.state.ontology_service, PMO)
    names = next(t for t in session.tools() if t["name"] == "suggest_action")["parameters"]["properties"]["actionType"]["enum"]
    assert "submitAiProposal" in names and not {"approveAiProposal", "rejectAiProposal", "closeAiRiskFlag"} & set(names)
    import json
    drafted = json.loads(session.run("suggest_action", json.dumps({"actionType": "submitAiProposal", "parameters": {
        "kind": "riskFlag", "project": "WDR101", "title": "预算可能不够", "basis": [budget_fact()]}})))
    assert drafted["precheck"] == "通过" and objects(app, "AiProposal") == {}, "a card only: nothing is submitted"
    assert session.suggestions[0]["basisTexts"] == ["项目 WDR101 的预算人天 ≥ 100（当前 120.00）"], "the card reads like the drawer"
    proposal = submit(app)
    refused = json.loads(session.run("suggest_action", json.dumps({"actionType": "approveAiProposal", "parameters": {"proposal": proposal}})))
    assert refused["precheck"] == "未通过" and "只能由人处理" in refused["reasons"][0]
    assert objects(app, "AiProposal")[proposal]["status"] == "pending"


def test_alert_and_signal_facts_are_checked_against_the_rules_outputs(app):
    past = (today(app) - timedelta(days=5)).isoformat()
    created = act(app, ADMIN, "createMilestone", project="WDR101", name="系统上线", plannedStart=(today(app) - timedelta(days=30)).isoformat(), plannedEnd=past)
    milestone = next(e["primaryKey"] for e in created["edits"] if e["objectType"] == "Milestone")
    alert = {"type": "alert", "project": "WDR101", "code": "MILESTONE_DUE"}
    act(app, ADMIN, "approveAiProposal", proposal=submit(app, title="上线延期拖累回款", basis=[alert]))
    flag = next(iter(objects(app, "AiRiskFlag").values()))
    assert flag["basis"][0]["text"].startswith("WDR101 预警「") and flag["basisHolds"]

    signals = app.state.ontology_service.execute_function(ADMIN, "departmentOperatingReview", {})["result"]["signals"]
    signal = {"type": "signal", "signal": signals[0]["signal"]}
    assert check(app, ADMIN, "submitAiProposal", kind="strategyNote", category="other", title="t", basis=[signal])["valid"]
    absent = check(app, ADMIN, "submitAiProposal", kind="strategyNote", category="other", title="t",
                   basis=[{"type": "signal", "signal": "staffing_support", "project": "ZZZ999"}])
    assert not absent["valid"] and any("不涉及 ZZZ999" in (c["reason"] or "") for c in absent["criteria"])

    act(app, ADMIN, "confirmMilestoneCompletion", milestone=milestone)
    assert app.state.ai_findings.refresh()["closed"] == 1
    closed = objects(app, "AiRiskFlag")[flag["flagId"]]
    assert closed["status"] == "closed" and "已没有预警 MILESTONE_DUE" in closed["closedReason"]
