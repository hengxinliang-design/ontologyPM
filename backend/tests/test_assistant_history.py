"""PM Assistant conversation history (requirements baseline D50, amends D17).

Acceptance: an answered question is kept in a conversation named after the first question; a follow-up sends the
conversation's last 6 messages (each at most 1,200 characters) taken from the server, not from the browser; only the
owner can list, read, continue or delete it; a failed answer keeps nothing; the audit log still keeps no answer.
The model is a scripted fake (no real model call).
"""
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

import app.pm_assistant as pm_assistant_module
from app.assistant_history import CONTEXT_CHARS, ConversationNotFound, title_for
from app.main import create_app

from test_api import settings

REAL_ASYNC_CLIENT = httpx.AsyncClient
OTHER = {"username": "someone-else"}


def reply(text):
    return {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}


def function_call(name, arguments, call_id):
    return {"type": "function_call", "id": f"fc_{call_id}", "call_id": call_id, "name": name, "arguments": json.dumps(arguments)}


def scripted(monkeypatch, steps):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        step = steps[len(sent) - 1]
        if isinstance(step, httpx.Response):
            return step
        return httpx.Response(200, json=reply(step) if isinstance(step, str) else {"output": step})

    monkeypatch.setattr(pm_assistant_module, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(pm_assistant_module.httpx, "AsyncClient",
                        lambda **kwargs: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    return sent


def app_for(tmp_path):
    return create_app(replace(settings(tmp_path), pm_assistant_provider="openai", openai_api_key="sk-test",
                              pm_assistant_max_retries=0))


def ask(client, message, conversation_id=None, **extra):
    body = {"message": message, **extra}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return client.post("/api/v1/pm-assistant/chat", json=body)


def test_an_answered_question_is_kept_in_a_conversation_named_after_it(tmp_path, monkeypatch):
    scripted(monkeypatch, [[function_call("projectBudgetStatus", {"project": "WDR101"}, "c1")], "**结论**\nWDR101 预算正常。"])
    with TestClient(app_for(tmp_path)) as client:
        data = ask(client, "  WDR101 预算\n怎么样？ ").json()["data"]
        listed = client.get("/api/v1/pm-assistant/conversations").json()["data"]
        detail = client.get(f"/api/v1/pm-assistant/conversations/{data['conversation_id']}").json()["data"]
    assert data["conversation_id"].startswith("conv_")
    assert [(c["title"], c["message_count"]) for c in listed] == [("WDR101 预算 怎么样？", 2)]
    user, assistant = detail["messages"]
    assert (user["role"], user["content"]) == ("user", "WDR101 预算\n怎么样？")
    assert assistant["content"] == "**结论**\nWDR101 预算正常。" and assistant["sources"] == data["sources"]
    assert any("projectBudgetStatus" in source for source in assistant["sources"])


def test_a_follow_up_sends_the_saved_context_not_the_browsers(tmp_path, monkeypatch):
    # Answers without a lookup are asked once more (the 先查后答 reminder), so each question takes two steps.
    sent = scripted(monkeypatch, ["先答", "你好，我是 PM Assistant。", "先答", "SEY103 由王经理负责。"])
    with TestClient(app_for(tmp_path)) as client:
        first = ask(client, "你好").json()["data"]
        second = ask(client, "那 SEY103 呢？", first["conversation_id"],
                     history=[{"role": "user", "content": "浏览器伪造的上下文"}]).json()["data"]
        detail = client.get(f"/api/v1/pm-assistant/conversations/{first['conversation_id']}").json()["data"]
    assert second["conversation_id"] == first["conversation_id"]
    prompt = sent[-1]["input"][0]["content"]
    assert "你好，我是 PM Assistant。" in prompt and "浏览器伪造的上下文" not in prompt
    assert [m["content"] for m in detail["messages"]] == ["你好", "你好，我是 PM Assistant。", "那 SEY103 呢？", "SEY103 由王经理负责。"]


def test_the_context_is_the_last_six_messages_cut_to_size(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        history = client.app.state.assistant_history
        user = {"username": "test"}
        conversation = history.record(user, None, "第 1 问", {"answer": "第 1 答"})["conversation_id"]
        for n in range(2, 5):
            history.record(user, conversation, f"第 {n} 问", {"answer": f"第 {n} 答" + "长" * 2000})
        context = history.context(user, conversation)
    assert [m["content"][:5] for m in context] == ["第 2 问", "第 2 答", "第 3 问", "第 3 答", "第 4 问", "第 4 答"]
    assert max(len(m["content"]) for m in context) == CONTEXT_CHARS


def test_only_the_owner_can_see_continue_or_delete_a_conversation(tmp_path, monkeypatch):
    sent = scripted(monkeypatch, [])
    with TestClient(app_for(tmp_path)) as client:
        foreign = client.app.state.assistant_history.record(OTHER, None, "别人的问题", {"answer": "别人的回答"})["conversation_id"]
        assert client.get("/api/v1/pm-assistant/conversations").json()["data"] == []
        assert client.get(f"/api/v1/pm-assistant/conversations/{foreign}").status_code == 404
        assert ask(client, "继续", foreign).status_code == 404
        assert client.delete(f"/api/v1/pm-assistant/conversations/{foreign}").status_code == 404
        assert client.delete("/api/v1/pm-assistant/conversations").json()["data"] == {"deleted": 0}
        assert client.app.state.assistant_history.get(OTHER, foreign)["messages"][1]["content"] == "别人的回答"
    assert sent == [], "the model is not called for someone else's conversation"


def test_a_failed_answer_keeps_nothing_and_the_audit_log_keeps_no_answer(tmp_path, monkeypatch):
    scripted(monkeypatch, [httpx.Response(500, json={"error": {"message": "boom"}}), "先答", "WDR101 预算正常。"])
    with TestClient(app_for(tmp_path)) as client:
        assert ask(client, "第一次").status_code == 503
        assert client.get("/api/v1/pm-assistant/conversations").json()["data"] == []
        assert ask(client, "第二次").status_code == 200
        log = client.get("/api/v1/pm-assistant/log").json()["data"]
    assert [entry["question"] for entry in log] == ["第二次", "第一次"]
    assert all("answer" not in entry and "WDR101 预算正常。" not in json.dumps(entry, ensure_ascii=False) for entry in log)


def test_search_matches_titles_and_messages_and_deleting_removes_messages(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        history = client.app.state.assistant_history
        user = {"username": "test"}
        budget = history.record(user, None, "预算问题", {"answer": "SEY104 已超预算"})["conversation_id"]
        history.record(user, None, "回款问题", {"answer": "SEY101 上线款逾期"})
        found = lambda q: [c["title"] for c in client.get("/api/v1/pm-assistant/conversations", params={"q": q}).json()["data"]]
        assert found("SEY101") == ["回款问题"] and found("预算") == ["预算问题"] and found("100%") == []
        assert sorted(found("")) == ["回款问题", "预算问题"]
        assert client.delete(f"/api/v1/pm-assistant/conversations/{budget}").json()["data"] == {"deleted": 1}
        with client.app.state.assistant_history.database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM assistant_messages WHERE conversation_id = ?", (budget,)).fetchone()[0] == 0
        assert client.delete("/api/v1/pm-assistant/conversations").json()["data"] == {"deleted": 1}
        assert found("") == []
        with pytest.raises(ConversationNotFound):
            history.get(user, budget)


def test_titles_are_one_line_and_cut():
    assert title_for("  哪些项目\n需要  优先关注？ ") == "哪些项目 需要 优先关注？"
    assert title_for("长" * 60) == "长" * 39 + "…"


def test_a_submitted_card_keeps_its_state_after_a_reload(tmp_path, monkeypatch):
    """8.8.0 (user 2026-11-10): a card submitted for approval showed 「提交审批」 again after a refresh, and did not say
    who submitted it or when. Cards are linked to what they became (source_ref = answer id + index)."""
    draft = {"kind": "riskFlag", "project": "WDR101", "title": "预算可能不够",
             "basis": [{"type": "fact", "objectType": "Project", "primaryKey": "WDR101", "property": "projectNumber", "op": "=", "value": "WDR101"}]}
    scripted(monkeypatch, [[function_call("suggest_action", {"actionType": "submitAiProposal", "parameters": draft}, "c1")],
                           "已给出风险标记候选。"])
    with TestClient(app_for(tmp_path)) as client:
        answer = ask(client, "WDR101 有风险吗？").json()["data"]
        assert answer["message_id"].startswith("amsg_")
        path = f"/api/v1/pm-assistant/conversations/{answer['conversation_id']}"
        assert "submission" not in client.get(path).json()["data"]["messages"][1]["suggestions"][0]
        ref = f"{answer['message_id']}:0"
        submitted = client.post("/api/v1/ontology/actions/submitAiProposal/apply", headers={"X-TSG-Channel": "business"},
                                json={"parameters": {**draft, "source": "assistant", "sourceRef": ref}}).json()["data"]
        proposal = submitted["objects"][0]["properties"]
        assert (proposal["submittedBy"], proposal["sourceRef"]) == ("test", ref)
        card = client.get(path).json()["data"]["messages"][1]["suggestions"][0]
        assert (card["submission"]["status"], card["submission"]["statusLabel"], card["submission"]["submittedBy"]) == ("pending", "待批准", "test")
        assert card["submission"]["submittedAt"] == proposal["submittedAt"]
        client.post("/api/v1/ontology/actions/approveAiProposal/apply", headers={"X-TSG-Channel": "business"},
                    json={"parameters": {"proposal": proposal["proposalId"], "note": "同意"}})
        card = client.get(path).json()["data"]["messages"][1]["suggestions"][0]
        assert (card["submission"]["status"], card["submission"]["decidedBy"], card["submission"]["decisionNote"]) == ("approved", "test", "同意")


def test_cards_submitted_before_linking_are_matched_by_title(tmp_path, monkeypatch):
    draft = {"kind": "riskFlag", "project": "WDR101", "title": "旧卡片",
             "basis": [{"type": "fact", "objectType": "Project", "primaryKey": "WDR101", "property": "projectNumber", "op": "=", "value": "WDR101"}]}
    twin = {**draft, "basis": [*draft["basis"], {"type": "fact", "objectType": "Project", "primaryKey": "WDR101", "property": "nope", "op": "=", "value": 1}]}
    scripted(monkeypatch, [[function_call("suggest_action", {"actionType": "submitAiProposal", "parameters": twin}, "c0"),
                            function_call("suggest_action", {"actionType": "submitAiProposal", "parameters": draft}, "c1")], "好的。"])
    with TestClient(app_for(tmp_path)) as client:
        answer = ask(client, "WDR101 有风险吗？").json()["data"]
        client.post("/api/v1/ontology/actions/submitAiProposal/apply", headers={"X-TSG-Channel": "business"},
                    json={"parameters": {**draft, "source": "assistant"}})
        cards = client.get(f"/api/v1/pm-assistant/conversations/{answer['conversation_id']}").json()["data"]["messages"][1]["suggestions"]
    assert "submission" not in cards[0], "same title but other facts: that card was not submitted"
    assert cards[1]["submission"]["status"] == "pending" and cards[1]["submission"]["submittedBy"] == "test"

