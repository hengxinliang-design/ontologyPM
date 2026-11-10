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


# ------------------------------------------------------------------ 8.7.0 (D56, D57), build package 2026-11-10-strategy-gaps
def stalled(code):
    return {"type": "fact", "objectType": "Project", "primaryKey": code, "property": "projectNumber", "op": "eq", "value": code}


def test_query_style_comparisons_are_read_as_the_same_comparison(app):
    for op, value in (("eq", 120), ("GTE", 100), ("ne", 1), ("lt", 500)):  # WDR101's budget is 120 person-days
        result = check(app, PMO, "submitAiProposal", kind="riskFlag", project="WDR101", title="t", basis=[budget_fact(op=op, value=value)])
        assert result["valid"], (op, result)
    proposal_id = submit(app, basis=[budget_fact(op="gte", value=100)])
    proposal = objects(app, "AiProposal")[proposal_id]
    assert proposal["basis"][0]["op"] == ">=" and "≥ 100" in proposal["basis"][0]["text"]
    assert not check(app, PMO, "submitAiProposal", kind="riskFlag", project="WDR101", title="t", basis=[budget_fact(op="~")])["valid"]


def test_an_uncovered_strategy_is_submitted_as_other_and_listed_as_a_gap(app):
    import json
    from app.pm_assistant import AssistantSession

    session = AssistantSession(app.state.ontology_service, PMO)
    session.run("suggest_action", json.dumps({"actionType": "submitAiProposal", "parameters": {
        "kind": "strategyNote", "category": "售前与商机管理", "title": "清理停滞商机", "basis": [stalled("WDR101")]}}, ensure_ascii=False))
    card = session.suggestions[-1]
    assert card["valid"], card
    assert (card["parameters"]["category"], card["parameters"]["proposedCategory"]) == ("other", "售前与商机管理")
    assert "不在现有策略类别中" in card["notes"][0]
    session.run("suggest_action", json.dumps({"actionType": "submitAiProposal", "parameters": {
        "kind": "strategyNote", "category": "争取新合同", "title": "争取二期", "basis": [stalled("WDR101")]}}, ensure_ascii=False))
    assert session.suggestions[-1]["parameters"]["category"] == "win_contracts" and session.suggestions[-1]["notes"] == [], \
        "a category written as its label is the category"

    first = submit(app, **{**card["parameters"]})
    second = submit(app, kind="strategyNote", project=None, category="other", proposedCategory="售前与商机管理", title="控制售前沉没投入",
                    basis=[stalled("TPL149")])
    other_gap = submit(app, kind="strategyNote", project=None, category="other", proposedCategory="人员培养", title="安排新人带教",
                       basis=[stalled("WDR101")])
    proposals = objects(app, "AiProposal")
    assert all(proposals[p]["status"] == "pending" for p in (first, second, other_gap)), "different gaps do not replace each other"
    act(app, HEAD, "approveAiProposal", proposal=first)
    act(app, HEAD, "rejectAiProposal", proposal=other_gap, note="不需要")
    review = app.state.ontology_service.execute_function(HEAD, "departmentOperatingReview", {})["result"]
    gaps = {g["proposedCategory"]: g for g in review["aiStrategyGaps"]}
    assert [g["proposedCategory"] for g in review["aiStrategyGaps"]] == ["售前与商机管理", "人员培养"]
    assert (gaps["售前与商机管理"]["count"], gaps["售前与商机管理"]["approved"], gaps["售前与商机管理"]["pending"]) == (2, 1, 1)
    assert (gaps["人员培养"]["count"], gaps["人员培养"]["rejected"]) == (1, 1)
    [note] = review["aiStrategyNotes"]
    assert (note["categoryLabel"], note["proposedCategory"]) == ("其他 · 售前与商机管理", "售前与商机管理")
    assert [p["categoryLabel"] for p in review["aiStrategyProposals"]] == ["其他 · 售前与商机管理"]


def test_a_proposed_category_needs_the_other_category(app):
    result = check(app, PMO, "submitAiProposal", kind="strategyNote", category="win_contracts", proposedCategory="售前",
                   title="t", basis=[stalled("WDR101")])
    assert not result["valid"] and any("只有类别为「其他」" in (c["reason"] or "") for c in result["criteria"])


def test_the_card_lists_every_problem_at_once(app):
    import json
    from app.pm_assistant import AssistantSession

    session = AssistantSession(app.state.ontology_service, PMO)
    session.run("suggest_action", json.dumps({"actionType": "submitAiProposal", "parameters": {
        "kind": "strategyNote", "category": "other", "title": "长" * 121, "basis": [budget_fact(op="~")]}}, ensure_ascii=False))
    reasons = session.suggestions[-1]["reasons"]
    assert any("标题" in r or "120" in r for r in reasons) and any("比较符" in r for r in reasons), reasons


def strategy_with_two_projects(app):
    """A 推动验收回款 strategy whose facts are about WDR101 (PM 刘浪) and TPL149."""
    return submit(app, kind="strategyNote", project=None, category="push_acceptance", title="集中推动验收回款",
                  detail="逐项目锁定验收与开票时间表", basis=[budget_fact(), budget_fact(op=">=", value=0, project="TPL149")])


def test_an_approved_strategy_is_broken_into_project_interventions(app):
    due = (today(app) + timedelta(days=14)).isoformat()
    proposal = strategy_with_two_projects(app)
    review = app.state.ontology_service.execute_function(HEAD, "departmentOperatingReview", {})["result"]
    assert review["aiStrategyProposals"][0]["citedProjects"] == ["TPL149", "WDR101"], "the projects come from the facts"
    result = act(app, HEAD, "approveAiProposal", proposal=proposal, projects=["WDR101", "TPL149"], dueDate=due)
    created = {e["primaryKey"] for e in result["edits"] if e["objectType"] == "Intervention" and e["operation"] == "create"}
    assert len(created) == 2, "the department head's approval creates them (D60)"
    interventions = {k: v for k, v in objects(app, "Intervention").items() if k in created}
    assert {(i["projectNumber"], i["interventionType"], i["dueDate"], i["sourceProposalId"]) for i in interventions.values()} == {
        ("WDR101", "payment", due, proposal), ("TPL149", "payment", due, proposal)}
    assert {i["projectNumber"]: i["owner"] for i in interventions.values()}["WDR101"] == "刘浪", "each project's PM owns it"
    assert all(i["title"] == "落实部门策略：集中推动验收回款" for i in interventions.values())

    def progress():
        [note] = app.state.ontology_service.execute_function(HEAD, "departmentOperatingReview", {})["result"]["aiStrategyNotes"]
        return note, sorted((f["projectNumber"], f["status"]) for f in note["followUps"])

    note, steps = progress()
    assert steps == [("TPL149", "open"), ("WDR101", "open")] and note["readyToClose"] is False
    wdr = next(k for k, v in interventions.items() if v["projectNumber"] == "WDR101")
    tpl = next(k for k, v in interventions.items() if v["projectNumber"] == "TPL149")
    overview = app.state.ontology_service.execute_function(ADMIN, "aiSuggestionOverview", {"project": "WDR101"})["result"]
    assert overview["interventionBasis"][wdr]["strategyTitle"] == "集中推动验收回款" and overview["interventionBasis"][wdr]["basisHolds"]

    act(app, PMO, "updateInterventionStatus", intervention=tpl, status="closed", conclusion="已安排验收")
    note, steps = progress()
    assert steps == [("TPL149", "closed"), ("WDR101", "open")] and note["readyToClose"] is False
    act(app, HEAD, "closeAiStrategyNote", note=note["noteId"], reason="本月已安排")
    hint = app.state.ontology_service.execute_function(ADMIN, "aiSuggestionOverview", {"project": "WDR101"})["result"]["interventionBasis"][wdr]
    assert hint["basisHolds"] is False and hint["basisNote"].startswith("所属策略已关闭") and "本月已安排" in hint["basisNote"]
    assert objects(app, "Intervention")[wdr]["status"] == "open", "a person closes it (D59)"


def test_all_follow_ups_closed_means_the_strategy_may_be_closed(app):
    proposal = strategy_with_two_projects(app)
    result = act(app, HEAD, "approveAiProposal", proposal=proposal, projects=["WDR101"], dueDate=(today(app) + timedelta(days=3)).isoformat())
    [intervention] = [e["primaryKey"] for e in result["edits"] if e["objectType"] == "Intervention"]
    act(app, PMO, "updateInterventionStatus", intervention=intervention, status="closed", conclusion="已完成")
    [note] = app.state.ontology_service.execute_function(HEAD, "departmentOperatingReview", {})["result"]["aiStrategyNotes"]
    assert note["readyToClose"] is True and note["status"] == "active", "it only says it may be closed"


def test_breaking_down_needs_projects_from_the_facts_and_a_due_date(app):
    proposal = strategy_with_two_projects(app)
    due = (today(app) + timedelta(days=5)).isoformat()

    def reason(**values):
        result = check(app, HEAD, "approveAiProposal", proposal=proposal, **values)
        return " ".join(c["reason"] or "" for c in result["criteria"] if not c["passed"])

    assert "ZMJ113 不在这条策略的依据" in reason(projects=["ZMJ113"], dueDate=due) and "可选：TPL149、WDR101" in reason(projects=["ZMJ113"], dueDate=due)
    assert "要填干预截止日期" in reason(projects=["WDR101"])
    assert "不能早于今天" in reason(projects=["WDR101"], dueDate=(today(app) - timedelta(days=1)).isoformat())
    assert "项目编号列表" in reason(projects="WDR101", dueDate=due)
    assert reason(projects=[], dueDate=due) == "", "approving without breaking down is allowed"
    flag = submit(app)
    flag_check = check(app, PMO, "approveAiProposal", proposal=flag, projects=["WDR101"], dueDate=due)
    assert any("只有 部门策略（AI）可以分解" in (c["reason"] or "") for c in flag_check["criteria"])


def test_an_adopted_strategy_can_be_broken_down_later_without_repeats(app):
    proposal = strategy_with_two_projects(app)
    act(app, HEAD, "approveAiProposal", proposal=proposal)
    [note] = app.state.ontology_service.execute_function(HEAD, "departmentOperatingReview", {})["result"]["aiStrategyNotes"]
    assert note["followUps"] == [] and note["citedProjects"] == ["TPL149", "WDR101"]
    due = (today(app) + timedelta(days=7)).isoformat()
    first = act(app, HEAD, "breakDownAiStrategyNote", note=note["noteId"], projects=["WDR101"], dueDate=due)
    assert [e["objectType"] for e in first["edits"]] == ["Intervention"]
    again = check(app, HEAD, "breakDownAiStrategyNote", note=note["noteId"], projects=["WDR101", "TPL149"], dueDate=due)
    assert any("WDR101 已有这条策略未关闭的落实干预" in (c["reason"] or "") for c in again["criteria"])
    assert not check(app, PMO, "breakDownAiStrategyNote", note=note["noteId"], projects=["TPL149"], dueDate=due)["valid"], "admin or department head"
    act(app, HEAD, "breakDownAiStrategyNote", note=note["noteId"], projects=["TPL149"], dueDate=due)
    [note] = app.state.ontology_service.execute_function(HEAD, "departmentOperatingReview", {})["result"]["aiStrategyNotes"]
    assert sorted(f["projectNumber"] for f in note["followUps"]) == ["TPL149", "WDR101"]


def test_object_lists_show_who_decided_and_never_repeat_the_title():
    """8.8.1 (user 2026-11-10): the AI 提案 list showed 标题 twice and no submitter or decision; 管理干预 had no 处理结论."""
    from app.ontology import load_ontology

    ontology = load_ontology()
    for name, wanted in {"AiProposal": {"submittedBy", "submittedAt", "decidedBy", "decidedAt", "status"},
                         "AiRiskFlag": {"approvedBy", "validUntil", "closedReason"},
                         "AiStrategyNote": {"approvedBy", "validUntil", "proposedCategory"},
                         "Intervention": {"status", "conclusion", "owner"}}.items():
        definition = ontology.object_type(name)
        columns = definition.list_properties
        assert definition.title_property not in columns, f"{name}: the title is already the first column"
        assert wanted <= set(columns), (name, columns)


def test_one_name_per_concept_for_ai_objects():
    """D64 (8.8.2): 「AI 建议」 and 「AI 策略建议」 named two different things; now the record and the strategy differ."""
    from pathlib import Path

    from app.ontology import load_ontology

    ontology = load_ontology()
    assert ontology.object_type("AiProposal").display_name == "AI 提案"
    assert ontology.object_type("AiStrategyNote").display_name == "部门策略（AI）"
    kinds = {v.api_name: dict(v.values) for v in ontology.value_types}["AiProposalKind"]
    assert kinds == {"intervention": "管理干预", "riskFlag": "风险标记", "strategyNote": "部门策略"}
    root = Path(__file__).resolve().parents[1] / "app"
    stale = [str(p) for p in root.rglob("*") if p.suffix in {".py", ".yaml"} and ("AI 建议" in p.read_text(encoding="utf-8") or "AI 策略建议" in p.read_text(encoding="utf-8"))]
    assert stale == []

