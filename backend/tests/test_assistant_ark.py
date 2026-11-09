"""PM Assistant on Volcengine Ark (豆包, TSG_PM_ASSISTANT_PROVIDER=ark).

Ark speaks the OpenAI Responses API (probed live 2026-11-09 with doubao-seed-evolving: the same payload, function_call
items and function_call_output round trip). These tests pin the address, the key and the error messages; the model is
a scripted fake.
"""
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

import app.pm_assistant as pm_assistant_module
from app.main import create_app
from app.pm_assistant import NOT_CONFIGURED

from test_api import settings

REAL_ASYNC_CLIENT = httpx.AsyncClient
URL = "https://ark.cn-beijing.volces.com/api/v3/responses"


def function_call(name, arguments, call_id):
    return {"type": "function_call", "id": f"fc_{call_id}", "call_id": call_id, "name": name, "arguments": json.dumps(arguments)}


def reply(text):
    return {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}


def scripted(monkeypatch, steps):
    sent = []

    def handler(request):
        sent.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": json.loads(request.content)})
        step = steps[len(sent) - 1]
        if isinstance(step, httpx.Response):
            return step
        return httpx.Response(200, json=reply(step) if isinstance(step, str) else {"output": step})

    monkeypatch.setattr(pm_assistant_module, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(pm_assistant_module.httpx, "AsyncClient",
                        lambda **kwargs: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    return sent


def ark(tmp_path, **overrides):
    values = {"pm_assistant_provider": "ark", "ark_api_key": "ark-test-key", "pm_assistant_model": "doubao-seed-evolving",
              "openai_api_key": None, **overrides}
    return create_app(replace(settings(tmp_path), **values))


def test_ark_uses_the_responses_wire_format_at_its_own_address(tmp_path, monkeypatch):
    sent = scripted(monkeypatch, [[function_call("projectBudgetStatus", {"project": "WDR101"}, "c1")], "**结论**\nWDR101 预算正常。"])
    with TestClient(ark(tmp_path)) as client:
        data = client.post("/api/v1/pm-assistant/chat", json={"message": "WDR101 预算怎么样？"}).json()["data"]
    assert data["answer"] == "**结论**\nWDR101 预算正常。" and (data["provider"], data["model"]) == ("ark", "doubao-seed-evolving")
    first, second = sent
    assert first["url"] == URL and first["auth"] == "Bearer ark-test-key"
    body = first["body"]
    assert body["model"] == "doubao-seed-evolving" and body["instructions"] and body["input"][0]["role"] == "user"
    assert body["reasoning"] == {"effort": "none"}, "accepted by Ark and keeps the answer fast (no reasoning tokens)"
    assert body["tools"][0]["type"] == "function" and "function" not in body["tools"][0], "Responses-format tools"
    outputs = [item for item in second["body"]["input"] if item.get("type") == "function_call_output"]
    assert outputs and outputs[0]["call_id"] == "c1" and "plannedPersonDays" in outputs[0]["output"]


def test_ark_base_url_can_be_changed(tmp_path, monkeypatch):
    sent = scripted(monkeypatch, ["你好。", "你好。"])
    with TestClient(ark(tmp_path, ark_base_url="https://ark.example.test/api/v3")) as client:
        assert client.post("/api/v1/pm-assistant/chat", json={"message": "你好"}).status_code == 200
    assert sent[0]["url"] == "https://ark.example.test/api/v3/responses"


def test_ark_without_a_key_is_not_configured_and_does_not_fall_back(tmp_path, monkeypatch):
    sent = scripted(monkeypatch, [])
    with TestClient(ark(tmp_path, ark_api_key=None, openai_api_key="sk-test")) as client:
        response = client.post("/api/v1/pm-assistant/chat", json={"message": "你好"})
    assert response.status_code == 503 and response.json()["detail"] == NOT_CONFIGURED and sent == []


@pytest.mark.parametrize(("response", "message"), [
    (httpx.Response(401, json={"error": {"code": "AuthenticationError", "message": "The API key format is incorrect."}}), "ARK_API_KEY"),
    (httpx.Response(404, json={"error": {"code": "InvalidEndpointOrModel.NotFound", "message": "model not found"}}), "未开通或名称有误"),
    (httpx.Response(403, json={"error": {"code": "ModelNotOpen", "message": "not activated"}}), "未开通或名称有误"),
    (httpx.Response(403, json={"error": {"code": "AccountOverdueError", "message": "overdue"}}), "欠费"),
    (httpx.Response(429, json={"error": {"code": "RateLimitExceeded.EndpointRPMExceeded", "message": "rpm"}}), "请求较多"),
])
def test_ark_failures_are_mapped(tmp_path, monkeypatch, response, message):
    scripted(monkeypatch, [response])
    with TestClient(ark(tmp_path)) as client:
        result = client.post("/api/v1/pm-assistant/chat", json={"message": "风险"})
    assert result.status_code == 503 and message in result.json()["detail"]
