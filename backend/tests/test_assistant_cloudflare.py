"""PM Assistant on Cloudflare Workers AI: protocol adaptation to the OpenAI-compatible chat completions endpoint.

The model is a scripted fake here, so these tests prove the wire format and the bounded loop only; the behaviour of
the real model is checked separately against the live service (configuration in docs/API.md, PM Assistant).
"""
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

import app.pm_assistant as pm_assistant_module
from app.main import create_app
from app.pm_assistant import NOT_CONFIGURED, TOO_MANY_STEPS

from test_api import settings
from test_roles_and_scope import with_project_manager

REAL_ASYNC_CLIENT = httpx.AsyncClient
ACCOUNT = "acct-test"
URL = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/v1/chat/completions"


def tool_call(name, arguments, call_id):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)}}


def completion(content=None, calls=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return {"id": "x", "object": "chat.completion", "choices": [{"index": 0, "message": message,
                                                                 "finish_reason": "tool_calls" if calls else "stop"}]}


def scripted(monkeypatch, steps):
    """Answer the n-th request with steps[n]: a list of tool calls, a final text, or an httpx.Response."""
    sent = []

    def handler(request):
        sent.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": json.loads(request.content)})
        step = steps(sent[-1]["body"], len(sent) - 1) if callable(steps) else steps[len(sent) - 1]
        if isinstance(step, httpx.Response):
            return step
        return httpx.Response(200, json=completion(step) if isinstance(step, str) else completion(None, step))

    monkeypatch.setattr(pm_assistant_module, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(pm_assistant_module.httpx, "AsyncClient",
                        lambda **kwargs: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    return sent


def cloudflare(tmp_path, **overrides):
    values = {"pm_assistant_provider": "cloudflare", "cloudflare_account_id": ACCOUNT, "cloudflare_api_token": "cf-test-token",
              "pm_assistant_model": "@cf/ibm-granite/granite-4.0-h-micro", "openai_api_key": None, **overrides}
    return create_app(replace(settings(tmp_path), **values))


def tool_messages(body):
    return {m["tool_call_id"]: json.loads(m["content"]) for m in body["messages"] if m["role"] == "tool"}


def test_chat_completions_wire_format_and_tool_round_trip(tmp_path, monkeypatch):
    sent = scripted(monkeypatch, [
        [tool_call("projectBudgetStatus", {"project": "TPL149"}, "c1")],
        "TPL149 的预算使用率见上。",
    ])
    with TestClient(cloudflare(tmp_path)) as client:
        response = client.post("/api/v1/pm-assistant/chat", json={"message": "TPL149 预算用了多少？"})
        assert response.status_code == 200, response.text
        data = response.json()["data"]
    first, second = sent
    assert first["url"] == URL and first["auth"] == "Bearer cf-test-token", "no OpenAI key is needed or sent"
    body = first["body"]
    assert body["model"] == "@cf/ibm-granite/granite-4.0-h-micro" and body["max_tokens"] == 1200
    assert not {"text", "reasoning", "store", "instructions", "input", "max_output_tokens"} & set(body), "OpenAI-only options are dropped"
    assert [m["role"] for m in body["messages"]] == ["system", "user"] and "PM Assistant" in body["messages"][0]["content"]
    assert body["tool_choice"] == "auto"
    budget = next(t for t in body["tools"] if t["function"]["name"] == "projectBudgetStatus")
    assert budget["type"] == "function" and set(budget["function"]) == {"name", "description", "parameters"}
    assert budget["function"]["parameters"]["required"] == ["project"]

    roles = [m["role"] for m in second["body"]["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assistant = second["body"]["messages"][2]
    assert assistant["tool_calls"][0]["id"] == "c1" and assistant["tool_calls"][0]["function"]["name"] == "projectBudgetStatus"
    assert "plannedPersonDays" in tool_messages(second["body"])["c1"], "the ontology function ran and answered by tool_call_id"
    assert data["answer"] == "TPL149 的预算使用率见上。" and data["provider"] == "cloudflare"
    assert "函数 项目预算消耗（projectBudgetStatus）" in data["sources"]


def test_bad_arguments_and_unknown_tools_go_back_as_errors(tmp_path, monkeypatch):
    sent = scripted(monkeypatch, [
        [tool_call("projectBudgetStatus", "{not json", "bad"), tool_call("dropTables", {}, "unknown"),
         {"id": "dict", "type": "function", "function": {"name": "projectBudgetStatus", "arguments": {"project": "TPL149"}}}],
        "参数有误，已改用正确的项目编号。",
    ])
    with TestClient(cloudflare(tmp_path)) as client:
        assert client.post("/api/v1/pm-assistant/chat", json={"message": "预算"}).status_code == 200
    results = tool_messages(sent[1]["body"])
    assert "参数无法解析" in results["bad"]["error"]
    assert "未知工具" in results["unknown"]["error"]
    assert "plannedPersonDays" in results["dict"], "arguments given as an object are accepted"


def test_tool_loop_is_bounded_and_final_answer_sends_no_tools(tmp_path, monkeypatch):
    def endless(body, index):
        if "tools" not in body:
            return "已取得的资料显示共有 3 个项目。<tool_call>{\"name\": \"query_objects\"}</tool_call>"
        return [tool_call("query_objects", {"objectSet": {"type": "base", "objectType": "Project"}}, f"c{index}")]

    sent = scripted(monkeypatch, endless)
    with TestClient(cloudflare(tmp_path)) as client:
        data = client.post("/api/v1/pm-assistant/chat", json={"message": "逐个看所有项目"}).json()["data"]
    assert len(sent) == pm_assistant_module.MAX_TOOL_ROUNDS + 1
    final = sent[-1]["body"]
    assert "tools" not in final and "tool_choice" not in final and "不能再调用工具" in final["messages"][0]["content"]
    assert data["answer"] == "已取得的资料显示共有 3 个项目。\n\n" + TOO_MANY_STEPS, "a tool call written as text is removed"


def test_tool_calls_written_as_text_are_run(tmp_path, monkeypatch):
    sent = scripted(monkeypatch, [
        '<tool_call>\n{"name": "projectBudgetStatus", "arguments": {"project": "WDR101"}}\n</tool_call>',
        "WDR101 的预算情况如上。",
    ])
    with TestClient(cloudflare(tmp_path)) as client:
        data = client.post("/api/v1/pm-assistant/chat", json={"message": "WDR101 预算"}).json()["data"]
    assert "plannedPersonDays" in tool_messages(sent[1]["body"])["text_call_0"]
    assert data["answer"] == "WDR101 的预算情况如上。"


def test_scope_markings_and_suggestions_are_unchanged(tmp_path, monkeypatch):
    sent = scripted(monkeypatch, [
        [tool_call("get_object", {"objectType": "Project", "primaryKey": "ZMJ113"}, "other"),
         tool_call("query_objects", {"objectSet": {"type": "base", "objectType": "Customer"}}, "customers"),
         tool_call("suggest_action", {"actionType": "openIntervention", "parameters": {"project": "TPL149", "title": "补充资源", "dueDate": "2026-12-31"}}, "write")],
        "ZMJ113 不在你的范围内；为 TPL149 建立干预需要你在业务页面执行。",
    ])
    with TestClient(cloudflare(tmp_path, auth_enabled=True)) as client:
        _, pm = with_project_manager(client)
        database = client.app.state.database
        before = database.dataset_versions()
        response = client.post("/api/v1/pm-assistant/chat", headers=pm, json={"message": "看看 ZMJ113，并给 TPL149 建一个干预"})
        assert response.status_code == 200, response.text
        assert database.dataset_versions() == before, "the assistant writes nothing"
    results = tool_messages(sent[1]["body"])
    assert "未找到" in results["other"]["error"] or "不存在" in results["other"]["error"]
    assert "无权" in results["customers"]["error"]
    assert results["write"]["note"].startswith("建议已展示给用户")
    [suggestion] = response.json()["data"]["suggestions"]
    assert (suggestion["actionType"], suggestion["valid"]) == ("openIntervention", True), "pre-checked, not executed"


def test_credentials_are_checked_per_provider_without_fallback(tmp_path, monkeypatch):
    sent = scripted(monkeypatch, ["不应调用"])
    for overrides in ({"cloudflare_api_token": None, "openai_api_key": "sk-would-work"},
                      {"cloudflare_account_id": None},
                      {"pm_assistant_provider": "nonsense", "openai_api_key": "sk-would-work"}):
        with TestClient(cloudflare(tmp_path, **overrides)) as client:
            chat = client.post("/api/v1/pm-assistant/chat", json={"message": "风险"})
            analysis = client.post("/api/v1/projects/TPL149/ai-analysis")
        assert chat.status_code == analysis.status_code == 503 and chat.json()["detail"] == NOT_CONFIGURED, overrides
    assert sent == [], "a missing Cloudflare credential never falls back to OpenAI"


def test_project_analysis_uses_chat_without_tools(tmp_path, monkeypatch):
    sent = scripted(monkeypatch, ["1. 风险判断：……"])
    with TestClient(cloudflare(tmp_path)) as client:
        response = client.post("/api/v1/projects/TPL149/ai-analysis")
        assert response.status_code == 200, response.text
    body = sent[0]["body"]
    assert "tools" not in body and [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "TPL149" in body["messages"][1]["content"]
    assert response.json()["data"]["answer"] == "1. 风险判断：……"


@pytest.mark.parametrize(("response", "message"), [
    (httpx.Response(401, json={"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]}), "授权不可用"),
    (httpx.Response(429, json={"errors": [{"code": 3036, "message": "You have used up your daily free allocation of 10,000 neurons"}]}), "Cloudflare Workers AI 的用量额度"),
    (httpx.Response(429, json={"errors": [{"code": 3040, "message": "Capacity temporarily exceeded"}]}), "请求较多"),
    (httpx.Response(400, json={"errors": [{"code": 5007, "message": "AiError: No such model: @cf/x"}]}), "模型配置无效"),
    (httpx.Response(200, json={"choices": []}), "无法解析"),
    (httpx.Response(200, json=completion("   ")), "未返回有效回答"),
])
def test_cloudflare_failures_are_mapped(tmp_path, monkeypatch, response, message):
    scripted(monkeypatch, lambda body, index: response)
    with TestClient(cloudflare(tmp_path)) as client:
        result = client.post("/api/v1/pm-assistant/chat", json={"message": "项目风险"})
    assert result.status_code == 503 and message in result.json()["detail"]


def test_retries_are_bounded(tmp_path, monkeypatch):
    sent = scripted(monkeypatch, lambda body, index: httpx.Response(503, json={}))
    with TestClient(cloudflare(tmp_path, pm_assistant_max_retries=2)) as client:
        assert client.post("/api/v1/pm-assistant/chat", json={"message": "风险"}).status_code == 503
    assert len(sent) == 3, "one try plus two retries"

    sent = scripted(monkeypatch, [httpx.Response(502, json={}), "恢复后的回答。"])
    with TestClient(cloudflare(tmp_path)) as client:
        assert client.post("/api/v1/pm-assistant/chat", json={"message": "风险"}).json()["data"]["answer"] == "恢复后的回答。"

    sent = scripted(monkeypatch, lambda body, index: httpx.Response(400, json={"errors": []}))
    with TestClient(cloudflare(tmp_path, pm_assistant_max_retries=3)) as client:
        assert client.post("/api/v1/pm-assistant/chat", json={"message": "风险"}).status_code == 503
    assert len(sent) == 1, "client errors are not retried"


def test_timeout_comes_from_settings(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(pm_assistant_module, "RETRY_BACKOFF_SECONDS", 0)

    def client_factory(**kwargs):
        seen.append(kwargs["timeout"])
        return REAL_ASYNC_CLIENT(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=completion("好的。"))), **kwargs)

    monkeypatch.setattr(pm_assistant_module.httpx, "AsyncClient", client_factory)
    with TestClient(cloudflare(tmp_path, pm_assistant_timeout_seconds=12.5)) as client:
        assert client.post("/api/v1/pm-assistant/chat", json={"message": "你好"}).status_code == 200
    assert seen == [12.5]


def test_openai_mode_is_unchanged_when_cloudflare_is_also_configured(tmp_path, monkeypatch):
    sent = []

    def handler(request):
        sent.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": "OpenAI 回答。"}]}]})

    monkeypatch.setattr(pm_assistant_module.httpx, "AsyncClient",
                        lambda **kwargs: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    with TestClient(cloudflare(tmp_path, pm_assistant_provider="openai", openai_api_key="sk-test", pm_assistant_model="gpt-5.2")) as client:
        data = client.post("/api/v1/pm-assistant/chat", json={"message": "你好"}).json()["data"]
    [(url, body)] = sent
    assert url == "https://api.openai.com/v1/responses" and data["provider"] == "openai" and data["answer"] == "OpenAI 回答。"
    assert body["text"] == {"verbosity": "low"} and body["reasoning"] == {"effort": "none"} and body["max_output_tokens"] == 1200
    assert body["tools"][0]["type"] == "function" and "name" in body["tools"][0] and "function" not in body["tools"][0]


def test_granite_argument_quirks_are_tolerated(tmp_path, monkeypatch):
    """Both seen against the live service: arguments encoded twice, and a text tool call with a stray closing brace."""
    twice = json.dumps(json.dumps({"project": "TPL149"}))
    sent = scripted(monkeypatch, [
        [tool_call("projectBudgetStatus", twice, "twice")],
        '<tool_call>\n{"name": "projectBudgetStatus", "arguments": "{\\"project\\":\\"WDR101\\"}"}}\n</tool_call>',
        "两个项目的预算情况如上。",
    ])
    with TestClient(cloudflare(tmp_path)) as client:
        data = client.post("/api/v1/pm-assistant/chat", json={"message": "TPL149 和 WDR101 的预算"}).json()["data"]
    assert "plannedPersonDays" in tool_messages(sent[1]["body"])["twice"]
    assert "plannedPersonDays" in tool_messages(sent[2]["body"])["text_call_0"]
    assert data["answer"] == "两个项目的预算情况如上。"
