"""Functional test of v1.39 (requirements baseline D50-D55, ontology 8.6.0): whole flows over HTTP, with login on.

Real role accounts (admin, PMO, project manager, department head, analyst) use the same endpoints and headers as the
pages: ontology actions on the business channel, ontology functions, the PM Assistant chat and conversations. The model
is a scripted fake, so no tokens are spent. Report: docs/test-reports/2026-11-10-v1.39-functional.md (cases F-A*, F-B*).
"""
import json
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

import app.pm_assistant as pm_assistant_module
from app.main import create_app

from test_api import settings
from test_roles_and_scope import BUSINESS, execute, login

REAL_ASYNC_CLIENT = httpx.AsyncClient
ADMIN_PASSWORD = "Tsnt@2026"  # the local default of the test settings (config.py), not a real credential


def fake_model(monkeypatch, steps):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        step = steps[len(sent) - 1]
        if isinstance(step, httpx.Response):
            return step
        body = {"output": [{"type": "message", "content": [{"type": "output_text", "text": step}]}]} if isinstance(step, str) \
            else {"output": step}
        return httpx.Response(200, json=body)

    monkeypatch.setattr(pm_assistant_module, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(pm_assistant_module.httpx, "AsyncClient",
                        lambda **kwargs: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    return sent


def call(name, arguments, call_id="c1"):
    return {"type": "function_call", "id": f"fc_{call_id}", "call_id": call_id, "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}


class World:
    """One app with five accounts; WDR101 and TPL149 belong to project manager 刘浪."""

    def __init__(self, client):
        self.client = client
        self.admin = login(client, "admin", ADMIN_PASSWORD)
        for project in ("WDR101", "TPL149"):
            assert client.patch(f"/api/v1/projects/{project}/settings", headers=self.admin,
                                json={"project_manager": "刘浪", "contract_type": "construction", "planned_person_days": "120"}).status_code == 200

        def account(username, role, **extra):
            created = client.post("/api/v1/users", headers=self.admin, json={"username": username, "password": "pass-" + username, "role": role, **extra})
            assert created.status_code == 201, created.text
            return login(client, username, "pass-" + username)

        self.pmo = account("pmo.f", "pmo")
        self.pm = account("pm.f", "project_manager", person_name="刘浪")
        self.head = account("head.f", "department_head")
        self.analyst = account("analyst.f", "analyst")

    def act(self, who, action, **parameters):
        return self.client.post(f"/api/v1/ontology/actions/{action}/apply", headers={**who, **BUSINESS}, json={"parameters": parameters})

    def submit(self, who, **fields):
        values = {"kind": "riskFlag", "project": "WDR101", "title": "预算可能不够", "basis": [budget(">=", 100)], **fields}
        response = self.act(who, "submitAiProposal", **values)
        assert response.status_code == 200, response.text
        return next(e["primaryKey"] for e in response.json()["data"]["edits"] if e["operation"] == "create")

    def objects(self, who, object_type):
        response = self.client.get(f"/api/v1/ontology/objects/{object_type}?pageSize=200", headers=who)
        return response.status_code, {row["primaryKey"]: row["properties"] for row in response.json().get("data") or []}

    def overview(self, who, project="WDR101"):
        return execute(self.client, who, "aiSuggestionOverview", {"project": project})

    def today(self):
        return self.client.app.state.project_repository.today()


def budget(op, value, project="WDR101"):
    return {"type": "fact", "objectType": "Project", "primaryKey": project, "property": "plannedPersonDays", "op": op, "value": value}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    app_settings = replace(settings(tmp_path), auth_enabled=True, pm_assistant_provider="openai", openai_api_key="sk-test",
                           pm_assistant_max_retries=0)
    with TestClient(create_app(app_settings)) as client:
        yield World(client)


# ------------------------------------------------------------------ F-A: PM Assistant page and history (D50)
def test_fa1_conversations_are_private_per_account(world, monkeypatch):
    fake_model(monkeypatch, ["先答", "WDR101 预算正常。", "先答", "TPL149 预算正常。"])
    c = world.client
    pmo_answer = c.post("/api/v1/pm-assistant/chat", headers=world.pmo, json={"message": "WDR101 预算怎么样？"})
    pm_answer = c.post("/api/v1/pm-assistant/chat", headers=world.pm, json={"message": "TPL149 预算怎么样？"})
    assert pmo_answer.status_code == pm_answer.status_code == 200
    pmo_id = pmo_answer.json()["data"]["conversation_id"]
    assert [x["title"] for x in c.get("/api/v1/pm-assistant/conversations", headers=world.pmo).json()["data"]] == ["WDR101 预算怎么样？"]
    assert [x["title"] for x in c.get("/api/v1/pm-assistant/conversations", headers=world.pm).json()["data"]] == ["TPL149 预算怎么样？"]
    assert c.get(f"/api/v1/pm-assistant/conversations/{pmo_id}", headers=world.pm).status_code == 404
    assert c.get(f"/api/v1/pm-assistant/conversations/{pmo_id}", headers=world.admin).status_code == 404, "admins do not read others' conversations"
    assert c.delete(f"/api/v1/pm-assistant/conversations/{pmo_id}", headers=world.pm).status_code == 404
    assert c.delete("/api/v1/pm-assistant/conversations", headers=world.pm).json()["data"] == {"deleted": 1}
    assert len(c.get("/api/v1/pm-assistant/conversations", headers=world.pmo).json()["data"]) == 1, "deleting all is per account"


def test_fa2_menus_and_audit(world, monkeypatch):
    fake_model(monkeypatch, ["先答", "好的。"])
    c = world.client
    for who in (world.head, world.analyst):  # no 项目管理 menu: no assistant, no conversations
        assert c.get("/api/v1/pm-assistant/conversations", headers=who).status_code == 403
        assert c.post("/api/v1/pm-assistant/chat", headers=who, json={"message": "你好"}).status_code == 403
    assert c.post("/api/v1/pm-assistant/chat", headers=world.pmo, json={"message": "你好"}).status_code == 200
    assert c.get("/api/v1/pm-assistant/log", headers=world.pmo).status_code == 403, "the audit log is for administrators"
    log = c.get("/api/v1/pm-assistant/log", headers=world.admin).json()["data"]
    assert log[0]["actor"] == "pmo.f" and log[0]["question"] == "你好" and "好的。" not in json.dumps(log, ensure_ascii=False)


def test_fa3_input_limits(world):
    c = world.client
    assert c.post("/api/v1/pm-assistant/chat", headers=world.pmo, json={"message": "  "}).status_code == 422
    assert c.post("/api/v1/pm-assistant/chat", headers=world.pmo, json={"message": "长" * 2001}).status_code == 422
    assert c.post("/api/v1/pm-assistant/chat", headers=world.pmo, json={"message": "x", "conversation_id": 5}).status_code == 422
    assert c.post("/api/v1/pm-assistant/chat", headers=world.pmo, json={"message": "x", "conversation_id": "conv_nope"}).status_code == 404
    assert c.get("/api/v1/pm-assistant/conversations?limit=0", headers=world.pmo).status_code == 422
    assert c.get("/api/v1/pm-assistant/conversations?q=" + "a" * 201, headers=world.pmo).status_code == 422
    assert c.get("/api/v1/pm-assistant/conversations?q=%25_", headers=world.pmo).json()["data"] == [], "LIKE wildcards are literal"


# ------------------------------------------------------------------ F-B: AI suggestions written back (D51-D55)
def test_fb1_assistant_card_is_saved_and_submitted_by_a_person(world, monkeypatch):
    draft = {"kind": "riskFlag", "project": "WDR101", "title": "预算可能不够", "detail": "已用 120 人天预算的一大半",
             "basis": [budget(">=", 100)]}
    fake_model(monkeypatch, [[call("projectBudgetStatus", {"project": "WDR101"}, "c1")],
                             [call("suggest_action", {"actionType": "submitAiProposal", "parameters": draft, "reason": "预算紧"}, "c2")],
                             "**结论** WDR101 预算偏紧，已给出风险标记候选。"])
    c = world.client
    answer = c.post("/api/v1/pm-assistant/chat", headers=world.pmo, json={"message": "WDR101 有什么风险？"}).json()["data"]
    [card] = answer["suggestions"]
    assert (card["actionType"], card["valid"]) == ("submitAiProposal", True)
    assert world.objects(world.admin, "AiProposal")[1] == {}, "a card writes nothing"
    saved = c.get(f"/api/v1/pm-assistant/conversations/{answer['conversation_id']}", headers=world.pmo).json()["data"]
    assert saved["messages"][1]["suggestions"][0]["parameters"]["title"] == "预算可能不够", "the card is kept in the history"
    proposal = world.submit(world.pmo, **card["parameters"], source="assistant", sourceRef=answer["conversation_id"])
    _, proposals = world.objects(world.admin, "AiProposal")
    assert (proposals[proposal]["status"], proposals[proposal]["sourceRef"]) == ("pending", answer["conversation_id"])


def test_fb2_approval_by_role(world):
    flag = world.submit(world.pmo)
    strategy = world.submit(world.pmo, kind="strategyNote", project=None, category="win_contracts", title="争取 WDR101 二期")
    # Department head: strategy yes, project item no (no 项目管理 menu → the role check refuses).
    refused = world.act(world.head, "approveAiProposal", proposal=flag)
    assert refused.status_code == 422 and "能维护该项目" in refused.text
    assert world.act(world.head, "approveAiProposal", proposal=strategy).status_code == 200
    # PMO cannot approve strategy notes; analyst can approve nothing.
    second = world.submit(world.pmo, kind="strategyNote", project=None, category="other", title="另一条策略")
    assert world.act(world.pmo, "approveAiProposal", proposal=second).status_code == 422
    assert world.act(world.analyst, "approveAiProposal", proposal=second).status_code == 422
    assert world.act(world.analyst, "approveAiProposal", proposal=flag).status_code in (403, 422)
    # The project's own manager approves a flag on their project; a submitter may approve their own (one person).
    assert world.act(world.pm, "approveAiProposal", proposal=flag).status_code == 200
    own = world.submit(world.pmo, title="PMO 自己提交自己批准", basis=[budget(">", 50)])
    assert world.act(world.pmo, "approveAiProposal", proposal=own).status_code == 200


def test_fb3_project_manager_scope(world):
    c = world.client
    outside = world.act(world.pm, "submitAiProposal", kind="riskFlag", project="ZMJ113", title="t", basis=[budget(">=", 0, "ZMJ113")])
    assert outside.status_code == 422 and "ZMJ113" in outside.text
    strategy = world.submit(world.pmo, kind="strategyNote", project=None, category="other", title="部门级")
    other = world.submit(world.pmo, project="ZMJ113", title="别人的项目", basis=[{"type": "fact", "objectType": "Project", "primaryKey": "ZMJ113", "property": "projectNumber", "op": "=", "value": "ZMJ113"}])
    status, visible = world.objects(world.pm, "AiProposal")
    assert status == 200 and strategy not in visible and other not in visible, "a PM sees suggestions of own projects only"
    assert world.act(world.pm, "approveAiProposal", proposal=other).status_code == 422
    assert world.act(world.pm, "approveAiProposal", proposal=strategy).status_code == 422
    assert execute(c, world.pm, "aiSuggestionOverview", {"project": "ZMJ113"}).status_code in (403, 404, 422)
    assert world.objects(world.pm, "AiStrategyNote")[0] == 403, "strategy notes are not readable for a PM"


def test_fb4_intervention_from_a_suggestion_lives_as_an_ordinary_intervention(world):
    c = world.client
    due = (world.today() + timedelta(days=10)).isoformat()
    proposal = world.submit(world.pmo, kind="intervention", title="与客户确认追加预算", dueDate=due, interventionType="resource")
    pending = world.overview(world.pmo).json()["data"]["result"]
    assert [p["proposalId"] for p in pending["pending"]] == [proposal] and pending["pending"][0]["canApprove"]
    risk = execute(c, world.pmo, "projectRiskAssessment", {"project": "WDR101"}).json()["data"]["result"]
    assert any(a["code"] == "AI_PROPOSAL_PENDING" for a in risk["anomalyAlerts"])
    assert world.act(world.pmo, "approveAiProposal", proposal=proposal, note="同意").status_code == 200
    overview = execute(c, world.pmo, "projectOverview", {"project": "WDR101"}).json()["data"]["result"]
    [intervention] = [i for i in overview["interventions"] if i["title"] == "与客户确认追加预算"]
    assert (intervention["owner"], intervention["due_date"], intervention["intervention_type"]) == ("刘浪", due, "resource")
    basis = world.overview(world.pmo).json()["data"]["result"]["interventionBasis"]
    assert basis[intervention["intervention_id"]]["basisHolds"] is True
    # The fact stops holding: the intervention is only marked; closing it through the drawer endpoint stops the watch.
    assert c.patch("/api/v1/projects/WDR101/settings", headers=world.admin, json={"planned_person_days": "60"}).status_code == 200
    world.client.app.state.ai_findings.refresh()
    marked = world.overview(world.pmo).json()["data"]["result"]["interventionBasis"][intervention["intervention_id"]]
    assert marked["basisHolds"] is False and "当前 60.00" in marked["basisNote"]
    closed = c.post(f"/api/v1/projects/WDR101/interventions/{intervention['intervention_id']}/status", headers=world.pmo, json={"status": "closed", "conclusion": "已追加预算"})
    assert closed.status_code == 200, closed.text
    assert world.client.app.state.ai_findings.refresh()["checked"] == 0, "a closed intervention is no longer rechecked"


def test_fb5_flag_life_cycle_recheck_expiry_renew_dedupe(world):
    c = world.client
    first = world.submit(world.pmo)
    again = world.submit(world.pmo, title="同一判断再提一次", basis=[budget(">=", 90)])
    _, proposals = world.objects(world.admin, "AiProposal")
    assert (proposals[first]["status"], proposals[again]["status"]) == ("superseded", "pending")
    assert world.act(world.pmo, "approveAiProposal", proposal=first).status_code == 422, "a superseded suggestion cannot be approved"
    assert world.act(world.pmo, "approveAiProposal", proposal=again).status_code == 200
    flag_id = world.overview(world.pmo).json()["data"]["result"]["riskFlags"][0]["flagId"]
    project = execute(c, world.pmo, "projectRiskAssessment", {"project": "WDR101"}).json()["data"]["result"]
    assert any(a["code"] == "AI_RISK_FLAG" for a in project["anomalyAlerts"])
    # Analyst (health menu) can read the flag but not close it.
    assert flag_id in world.objects(world.analyst, "AiRiskFlag")[1]
    assert world.act(world.analyst, "closeAiRiskFlag", flag=flag_id).status_code in (403, 422)
    # Renew, then expire by date.
    assert world.act(world.pm, "renewAiRiskFlag", flag=flag_id).status_code == 200
    refresh = c.app.state.ai_findings.refresh
    assert refresh(world.today() + timedelta(days=29))["expired"] == 0
    assert refresh(world.today() + timedelta(days=31))["expired"] == 1
    assert world.overview(world.pmo).json()["data"]["result"]["riskFlags"] == []
    assert world.act(world.pm, "renewAiRiskFlag", flag=flag_id).status_code == 422


def test_fb6_pending_suggestions_expire_and_cannot_be_approved_late(world):
    c = world.client
    assert c.patch("/api/v1/worktime-baseline", headers=world.admin, json={"ai_proposal_pending_days": "0"}).status_code == 422, \
        "the settings path uses the same range as the action"
    assert c.patch("/api/v1/worktime-baseline", headers=world.admin, json={"ai_proposal_pending_days": "2"}).status_code == 200
    proposal = world.submit(world.pmo)
    _, proposals = world.objects(world.admin, "AiProposal")
    assert proposals[proposal]["expiresOn"] == (world.today() + timedelta(days=2)).isoformat()
    assert c.app.state.ai_findings.refresh(world.today() + timedelta(days=3))["expired"] == 1
    late = world.act(world.pmo, "approveAiProposal", proposal=proposal)
    assert late.status_code == 422 and "已过期" in late.text


def test_fb7_input_boundaries(world):
    def reasons(**fields):
        values = {"kind": "riskFlag", "project": "WDR101", "title": "t", "basis": [budget(">=", 100)], **fields}
        response = world.act(world.pmo, "submitAiProposal", **values)
        return response.status_code, response.text

    assert reasons(title="长" * 121)[0] == 422
    assert reasons(title="   ")[0] == 422
    assert "最多 5 条" in reasons(basis=[budget(">=", 100)] * 6)[1]
    assert reasons(basis=json.dumps([budget(">=", 100)]))[0] == 200, "a JSON string is accepted"
    assert "比较符" in reasons(basis=[{**budget(">=", 100), "op": "~"}])[1]
    assert "值只能是" in reasons(basis=[{**budget(">=", 100), "value": {"a": 1}}])[1]
    assert "来源只能是" in reasons(source="email")[1]
    assert "不能早于今天" in reasons(kind="intervention", dueDate=(world.today() - timedelta(days=1)).isoformat())[1]
    assert "必须选择类别" in reasons(kind="strategyNote", project=None)[1]
    assert reasons(kind="nonsense")[0] == 422
    text_fact = {"type": "fact", "objectType": "Project", "primaryKey": "WDR101", "property": "projectManager", "op": ">", "value": "刘"}
    assert "依据不再成立" in reasons(basis=[text_fact])[1], "text compares only with = and !="
    assert reasons(basis=[{**text_fact, "op": "=", "value": "刘浪"}])[0] == 200


def test_fb8_ontology_browser_channel_stays_admin_only(world):
    proposal = world.submit(world.pmo)
    browser = world.client.post("/api/v1/ontology/actions/approveAiProposal/apply", headers=world.pmo, json={"parameters": {"proposal": proposal}})
    assert browser.status_code == 403, "D12: actions in the ontology browser are for administrators"
    assert world.client.post("/api/v1/ontology/actions/approveAiProposal/apply", headers=world.admin,
                             json={"parameters": {"proposal": proposal}}).status_code == 200


def test_fb9_strategy_notes_on_the_department_page(world):
    c = world.client
    note = world.submit(world.pmo, kind="strategyNote", project=None, category="win_contracts", title="争取 WDR101 二期")
    for who, can in ((world.head, True), (world.admin, True), (world.analyst, False), (world.pmo, False)):
        review = execute(c, who, "departmentOperatingReview", {}).json()["data"]["result"]
        assert [p["proposalId"] for p in review["aiStrategyProposals"]] == [note] and review["canApproveStrategy"] is can, who
    assert world.act(world.head, "approveAiProposal", proposal=note).status_code == 200
    review = execute(c, world.head, "departmentOperatingReview", {}).json()["data"]["result"]
    [active] = review["aiStrategyNotes"]
    assert (active["categoryLabel"], active["approvedBy"]) == ("争取新合同", "head.f")
    assert all(s["signal"] in {"push_acceptance", "control_cost", "staffing_support", "win_contracts", "release_resources", "hold"} for s in review["signals"])
    assert world.act(world.head, "closeAiStrategyNote", note=active["noteId"], reason="已安排").status_code == 200
    assert execute(c, world.head, "departmentOperatingReview", {}).json()["data"]["result"]["aiStrategyNotes"] == []


def test_fb10_project_analysis_items_carry_the_alert_code(world, monkeypatch):
    c = world.client
    past = (world.today() - timedelta(days=3)).isoformat()
    assert c.post("/api/v1/projects/WDR101/milestones", headers=world.pmo, json={"name": "系统上线", "planned_end": past}).status_code in (200, 201)
    fake_model(monkeypatch, ['{"conclusion": "上线已延期", "findings": [], "actions": {"I1": "刘浪在本周与客户确认新的上线日期"}}'])
    analysis = c.post("/api/v1/projects/WDR101/ai-analysis", headers=world.pmo)
    assert analysis.status_code == 200, analysis.text
    items = analysis.json()["data"]["review"]["items"]
    milestone_item = next(i for i in items if i["code"] == "MILESTONE_DUE")
    proposal = world.submit(world.pmo, kind="intervention", title="与客户确认新的上线日期", source="projectAnalysis",
                            dueDate=(world.today() + timedelta(days=7)).isoformat(),
                            basis=[{"type": "alert", "project": "WDR101", "code": milestone_item["code"]}])
    _, proposals = world.objects(world.admin, "AiProposal")
    assert proposals[proposal]["source"] == "projectAnalysis" and "预警" in proposals[proposal]["basis"][0]["text"]


def test_fb11_the_recheck_only_writes_when_something_changed(world):
    world.submit(world.pmo)
    database = world.client.app.state.database
    before = database.dataset_versions()["ai_proposals"]
    world.client.app.state.ai_findings.refresh()
    world.client.app.state.ai_findings.refresh()
    assert database.dataset_versions()["ai_proposals"] == before, "an unchanged hourly check must not invalidate the object index"


def test_fb13_one_unreadable_item_does_not_stop_the_recheck(world):
    broken, healthy = world.submit(world.pmo), world.submit(world.pmo, kind="intervention", title="干预", dueDate=(world.today() + timedelta(days=5)).isoformat())
    with world.client.app.state.database.connect() as connection:
        connection.execute("UPDATE ai_proposals SET basis_json = '{not json' WHERE proposal_id = ?", (broken,))
        connection.commit()
    result = world.client.app.state.ai_findings.refresh()
    _, proposals = world.objects(world.admin, "AiProposal")
    assert result["expired"] == 1 and proposals[broken]["status"] == "expired" and "依据无法读取" in proposals[broken]["decisionNote"]
    assert proposals[healthy]["status"] == "pending"


def test_fb14_overview_without_a_project_follows_the_scope(world):
    mine = world.submit(world.pmo)
    other = world.submit(world.pmo, project="ZMJ113", title="别人的", basis=[{"type": "fact", "objectType": "Project", "primaryKey": "ZMJ113", "property": "projectNumber", "op": "=", "value": "ZMJ113"}])
    pm_view = execute(world.client, world.pm, "aiSuggestionOverview", {}).json()["data"]["result"]
    pmo_view = execute(world.client, world.pmo, "aiSuggestionOverview", {}).json()["data"]["result"]
    assert [p["proposalId"] for p in pm_view["pending"]] == [mine]
    assert {p["proposalId"] for p in pmo_view["pending"]} == {mine, other}


def test_fb15_approval_rechecks_facts_that_changed_after_submission(world):
    proposal = world.submit(world.pmo)
    assert world.client.patch("/api/v1/projects/WDR101/settings", headers=world.admin, json={"planned_person_days": "80"}).status_code == 200
    late = world.act(world.pmo, "approveAiProposal", proposal=proposal)
    assert late.status_code == 422 and "当前 80.00" in late.text
    assert world.act(world.pmo, "rejectAiProposal", proposal=proposal, note="依据已变").status_code == 200, "it can still be rejected"


def test_fb16_the_recheck_closes_only_what_no_longer_holds(world):
    tpl = {"type": "fact", "objectType": "Project", "primaryKey": "TPL149", "property": "plannedPersonDays", "op": ">=", "value": 100}
    for proposal in (world.submit(world.pmo), world.submit(world.pmo, project="TPL149", basis=[tpl])):
        assert world.act(world.pmo, "approveAiProposal", proposal=proposal).status_code == 200
    assert world.client.patch("/api/v1/projects/TPL149/settings", headers=world.admin, json={"planned_person_days": "50"}).status_code == 200
    assert world.client.app.state.ai_findings.refresh()["closed"] == 1
    _, flags = world.objects(world.admin, "AiRiskFlag")
    assert {f["projectNumber"]: f["status"] for f in flags.values()} == {"WDR101": "active", "TPL149": "closed"}


def test_fb17_a_department_head_breaks_a_strategy_into_project_interventions(world):
    """8.7.0 (D58-D60): the head adopts a strategy into interventions on the cited projects; the PM sees and closes them."""
    c = world.client
    proposal = world.submit(world.pmo, kind="strategyNote", project=None, category="push_acceptance", title="集中推动验收回款",
                            basis=[budget(">=", 100), budget(">=", 0, "TPL149")])
    due = (world.today() + timedelta(days=10)).isoformat()
    assert world.act(world.head, "approveAiProposal", proposal=proposal, projects=["WDR101", "ZMJ113"], dueDate=due).status_code == 422
    assert world.act(world.head, "approveAiProposal", proposal=proposal, projects=["WDR101", "TPL149"], dueDate=due).status_code == 200
    basis = world.overview(world.pm).json()["data"]["result"]["interventionBasis"]
    [(intervention, link)] = [(k, v) for k, v in basis.items() if v.get("strategyTitle")][:1]
    assert link["strategyTitle"] == "集中推动验收回款" and link["basisHolds"]
    overview = execute(c, world.pm, "projectOverview", {"project": "WDR101"}).json()["data"]["result"]
    mine = next(i for i in overview["interventions"] if i["intervention_id"] == intervention)
    assert (mine["owner"], mine["intervention_type"]) == ("刘浪", "payment")
    closed = c.post(f"/api/v1/projects/WDR101/interventions/{intervention}/status", headers=world.pm, json={"status": "closed", "conclusion": "客户已确认验收计划"})
    assert closed.status_code == 200, "the PM carries it out and closes it"
    [note] = execute(c, world.head, "departmentOperatingReview", {}).json()["data"]["result"]["aiStrategyNotes"]
    assert sorted((f["projectNumber"], f["status"]) for f in note["followUps"]) == [("TPL149", "open"), ("WDR101", "closed")]


def test_fb18_history_and_closing_conclusion_over_http(world):
    """8.8.0 (D61-D63): the PM must write a conclusion to close; the timeline and past strategies show what happened."""
    c = world.client
    proposal = world.submit(world.pmo, kind="strategyNote", project=None, category="push_acceptance", title="集中推动验收回款",
                            basis=[budget(">=", 100)])
    approved = world.act(world.head, "approveAiProposal", proposal=proposal, projects=["WDR101"], dueDate=(world.today() + timedelta(days=5)).isoformat())
    intervention = next(e["primaryKey"] for e in approved.json()["data"]["edits"] if e["objectType"] == "Intervention")
    note = next(e["primaryKey"] for e in approved.json()["data"]["edits"] if e["objectType"] == "AiStrategyNote")
    status = f"/api/v1/projects/WDR101/interventions/{intervention}/status"
    refused = c.post(status, headers=world.pm, json={"status": "closed"})
    assert refused.status_code == 422 and "处理结论" in refused.text
    assert c.post(status, headers=world.pm, json={"status": "closed", "conclusion": "客户已签验收单"}).status_code == 200
    timeline = execute(c, world.pm, "interventionHistory", {"intervention": intervention}).json()["data"]["result"]
    assert [e["title"] for e in timeline["events"]][-1] == "状态改为「已关闭」" and timeline["conclusion"] == "客户已签验收单"
    assert timeline["origin"]["type"] == "strategy"
    assert execute(c, world.analyst, "interventionHistory", {"intervention": intervention}).status_code == 403
    assert world.act(world.head, "closeAiStrategyNote", note=note, reason="已落实").status_code == 200
    [past] = execute(c, world.head, "departmentOperatingReview", {}).json()["data"]["result"]["aiStrategyHistory"]
    assert past["followUps"][0]["conclusion"] == "客户已签验收单"
