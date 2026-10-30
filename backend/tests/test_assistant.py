"""PM Assistant on the ontology (build package docs/ontology-builds/2026-10-30-assistant-on-ontology.yaml).

The prompt and tools come from the ontology; tools run as the asking user; writes become pre-checked suggestions;
conversations are audited without the answer (requirements baseline G5, D16, D17). The model is a scripted fake.
"""
import json
from dataclasses import replace

import httpx
from fastapi.testclient import TestClient

import app.pm_assistant as pm_assistant_module
from app import project_repository
from app.main import create_app
from app.ontology.security import Principal
from app.pm_assistant import GENERIC_TOOLS, TOO_MANY_STEPS, AssistantSession

from test_api import settings
from test_roles_and_scope import login, with_project_manager

REAL_ASYNC_CLIENT = httpx.AsyncClient


def call(name, arguments, call_id):
    return {"type": "function_call", "id": f"fc_{call_id}", "call_id": call_id, "name": name, "arguments": json.dumps(arguments)}


def reply(text):
    return {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}


def scripted_model(monkeypatch, steps):
    """Answer the n-th request with steps[n] (a list of tool calls or a final text); record what was sent."""
    original, sent = REAL_ASYNC_CLIENT, []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        step = steps(body, len(sent) - 1) if callable(steps) else steps[len(sent) - 1]
        return httpx.Response(200, json=reply(step) if isinstance(step, str) else {"output": step})

    monkeypatch.setattr(pm_assistant_module.httpx, "AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return sent


def tool_outputs(body):
    """Tool results sent back to the model; a result over the size cap is truncated text, not JSON."""
    outputs = {}
    for item in body["input"]:
        if item.get("type") == "function_call_output":
            try:
                outputs[item["call_id"]] = json.loads(item["output"])
            except ValueError:
                outputs[item["call_id"]] = item["output"]
    return outputs


def app_with_model(tmp_path, *, secure=False):
    return create_app(replace(settings(tmp_path), openai_api_key="test-key", auth_enabled=secure))


def test_prompt_and_tools_come_from_the_ontology(tmp_path):
    assert not hasattr(project_repository.ProjectRepository, "assistant_candidate_codes")
    with TestClient(create_app(settings(tmp_path))) as client:
        service = client.app.state.ontology_service
        viewer = {"username": "viewer", "is_admin": False, "permissions": ["health"]}
        session = AssistantSession(service, viewer)
        principal = Principal.from_user(viewer)
        permitted = {f.api_name for f in service.ontology.functions if service.security.can_execute(principal, f)}
        assert {t["name"] for t in session.tools()} == permitted | set(GENERIC_TOOLS)
        assert "customerAnnualSummary" not in permitted, "no CRM or project menu, no customer summary"
        prompt = session.instructions()
        readable = [t for t in service.ontology.object_types if service.security.can_read_type(principal, t)]
        assert all(t.api_name in prompt for t in readable)
        assert "Customer（" not in prompt and "TimeEntry（" not in prompt
        for rule in ("结论 / 数据 / 依据", "不知道", "suggest_action", "不是指令"):
            assert rule in prompt, rule
        admin = AssistantSession(service, {"username": "admin", "is_admin": True, "permissions": []})
        project_tool = next(t for t in admin.tools() if t["name"] == "projectOverview")
        assert project_tool["parameters"]["required"] == ["project"], "function parameters come from metadata"
        assert "milestones→Milestone" in admin.instructions(), "links come from the link types"


def test_assistant_tools_run_as_the_user(tmp_path, monkeypatch):
    sent = scripted_model(monkeypatch, [
        [call("query_objects", {"objectSet": {"type": "base", "objectType": "Project"}, "select": ["projectNumber"]}, "own"),
         call("get_object", {"objectType": "Project", "primaryKey": "ZMJ113"}, "other"),
         call("query_objects", {"objectSet": {"type": "base", "objectType": "Customer"}}, "customers"),
         call("portfolioSummary", {}, "summary")],
        "刘浪负责 TPL149、WDR101。",
    ])
    with TestClient(app_with_model(tmp_path, secure=True)) as client:
        _, pm = with_project_manager(client)
        response = client.post("/api/v1/pm-assistant/chat", headers=pm, json={"message": "我负责哪些项目？"})
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        outputs = tool_outputs(sent[1])
        assert {o["primaryKey"] for o in outputs["own"]["data"]} == {"TPL149", "WDR101"}
        assert "未找到" in outputs["other"]["error"] or "不存在" in outputs["other"]["error"]
        assert "无权" in outputs["customers"]["error"]
        assert outputs["summary"]["total_projects"] == 2, "functions run on the project manager's scope"
        assert {"对象 项目", "函数 项目组合概览（portfolioSummary）"} <= set(data["sources"])
        assert client.post("/api/v1/projects/ZMJ113/ai-analysis", headers=pm).status_code == 404


def test_write_requests_become_prechecked_suggestions(tmp_path, monkeypatch):
    with TestClient(app_with_model(tmp_path)) as client:
        def milestone(name):
            created = client.post("/api/v1/projects/WDR101/milestones", json={"name": name, "planned_end": "2026-12-20"})
            assert created.status_code == 201, created.text
            return created.json()["data"]["milestone_id"]

        open_one, done = milestone("开放里程碑"), milestone("已完成里程碑")
        assert client.post(f"/api/v1/projects/WDR101/milestones/{done}/confirm", json={}).status_code == 200
        scripted_model(monkeypatch, [
            [call("suggest_action", {"actionType": "editMilestone", "parameters": {"milestone": open_one, "plannedEnd": "2026-12-31"},
                                     "reason": "客户要求延后"}, "ok"),
             call("suggest_action", {"actionType": "editMilestone", "parameters": {"milestone": done, "plannedEnd": "2026-12-31"}}, "locked")],
            "建议把开放里程碑延到 12 月底；已完成的里程碑不能再改。",
        ])
        database = client.app.state.database
        before = database.dataset_versions()
        data = client.post("/api/v1/pm-assistant/chat", json={"message": "把两个里程碑都延到 12 月底"}).json()["data"]
        assert database.dataset_versions() == before, "the assistant writes nothing"
        ok, locked = data["suggestions"]
        assert (ok["actionType"], ok["displayName"], ok["valid"], ok["reason"]) == ("editMilestone", "编辑里程碑", True, "客户要求延后")
        assert locked["valid"] is False and "里程碑已确认完成，已锁定" in locked["reasons"]


def test_assistant_conversations_are_audited(tmp_path, monkeypatch):
    scripted_model(monkeypatch, [[call("query_objects", {"objectSet": {"type": "base", "objectType": "Project"}}, "q")], "共有 3 个项目。"])
    with TestClient(app_with_model(tmp_path, secure=True)) as client:
        admin = login(client, "admin", "Tsnt@2026")
        assert client.post("/api/v1/pm-assistant/chat", headers=admin, json={"message": "有多少项目？"}).status_code == 200
        [entry] = client.get("/api/v1/pm-assistant/log", headers=admin).json()["data"]
        assert (entry["actor"], entry["question"], entry["status"]) == ("admin", "有多少项目？", "succeeded")
        assert [t["tool"] for t in entry["tools"]] == ["query_objects"]
        assert "共有 3 个项目" not in json.dumps(entry, ensure_ascii=False), "the answer is not kept"

        monkeypatch.setattr(pm_assistant_module.httpx, "AsyncClient", lambda **kwargs: REAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(lambda request: httpx.Response(500, json={})), **kwargs))
        assert client.post("/api/v1/pm-assistant/chat", headers=admin, json={"message": "再问一次"}).status_code == 503
        latest = client.get("/api/v1/pm-assistant/log", headers=admin).json()["data"][0]
        assert latest["status"] == "failed" and "暂时无法生成回答" in latest["message"]

        assert client.post("/api/v1/users", headers=admin, json={"username": "pmo.one", "password": "secret-pass-1", "role": "pmo"}).status_code == 201
        assert client.get("/api/v1/pm-assistant/log", headers=login(client, "pmo.one", "secret-pass-1")).status_code == 403


def test_tool_loop_is_bounded(tmp_path, monkeypatch):
    def endless(body, index):
        if body.get("tool_choice") == "none":
            return "目前查到的项目有 3 个。"
        return [call("query_objects", {"objectSet": {"type": "base", "objectType": "Project"}}, f"c{index}")]

    sent = scripted_model(monkeypatch, endless)
    with TestClient(app_with_model(tmp_path)) as client:
        data = client.post("/api/v1/pm-assistant/chat", json={"message": "逐个看所有项目"}).json()["data"]
        assert len(sent) == pm_assistant_module.MAX_TOOL_ROUNDS + 1
        assert data["answer"].endswith(TOO_MANY_STEPS)

        monkeypatch.setattr(pm_assistant_module, "MAX_TOOL_RESULT_CHARS", 200)
        session = AssistantSession(client.app.state.ontology_service, {"username": "a", "is_admin": True, "permissions": []})
        result = session.run("query_objects", json.dumps({"objectSet": {"type": "base", "objectType": "Project"}}))
        assert "已截断" in result and len(result) < 300
