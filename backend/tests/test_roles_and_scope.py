"""Roles and data scope (ontology 7.7.0, build package docs/ontology-builds/2026-10-30-roles-and-data-scope.yaml).

A project manager sees and maintains only their own projects (requirements baseline D2, D10); the scope is applied
once, in the ontology context. Ontology-browser actions are for administrators (D12).
"""
from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology import load_ontology

from test_api import settings

BUSINESS = {"X-TSG-Channel": "business"}
OWN = {"TPL149", "WDR101"}


def login(client, username, password):
    token = client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()["data"]["token"]
    return {"Authorization": "Bearer " + token}


def secure_client(tmp_path):
    return TestClient(create_app(replace(settings(tmp_path), auth_enabled=True)))


def with_project_manager(client):
    """Admin hands TPL149 and WDR101 to 刘浪 and creates his project-manager account."""
    admin = login(client, "admin", "Tsnt@2026")
    for project in OWN:
        assert client.patch(f"/api/v1/projects/{project}/settings", headers=admin, json={"project_manager": "刘浪"}).status_code == 200
    created = client.post("/api/v1/users", headers=admin, json={
        "username": "liulang", "password": "liulang-pass-1", "role": "project_manager", "person_name": "刘浪"})
    assert created.status_code == 201, created.text
    return admin, login(client, "liulang", "liulang-pass-1")


def execute(client, headers, function, parameters=None):
    return client.post(f"/api/v1/ontology/functions/{function}/execute", headers=headers, json={"parameters": parameters or {}})


def project_numbers(rows):
    return {row.get("project_number") or row.get("projectNumber") for row in rows}


def test_roles_set_scope_and_default_menus(tmp_path):
    with secure_client(tmp_path) as client:
        admin = login(client, "admin", "Tsnt@2026")

        def create(**payload):
            return client.post("/api/v1/users", headers=admin, json={"password": "secret-pass-1", **payload})

        missing_name = create(username="pm.one", role="project_manager")
        assert missing_name.status_code == 422 and "必须填写姓名" in missing_name.text
        wide_menus = create(username="pm.one", role="project_manager", person_name="张三", permissions=["projects", "crm"])
        assert wide_menus.status_code == 422 and "本人项目" in wide_menus.text

        pmo = create(username="pmo.one", role="pmo")
        assert pmo.status_code == 201
        assert set(pmo.json()["data"]["permissions"]) == {"projects", "investment", "department", "resource", "crm", "employeeWorktime", "sync"}
        assert pmo.json()["data"]["data_scope"] == "all" and pmo.json()["data"]["role_label"] == "PMO"

        assert create(username="pm.one", role="project_manager", person_name="张三").status_code == 201
        duplicate = create(username="pm.two", role="project_manager", person_name="张三")
        assert duplicate.status_code == 422 and "已被账号" in duplicate.text

        me = client.get("/api/v1/auth/me", headers=login(client, "pm.one", "secret-pass-1")).json()["data"]
        assert (me["role"], me["person_name"], me["data_scope"], me["permissions"]) == ("project_manager", "张三", "own", ["projects"])

        # Former clients without a role keep their menus and see all projects ("未设角色").
        legacy = create(username="legacy.one", permissions=["health"]).json()["data"]
        assert (legacy["role"], legacy["role_label"], legacy["data_scope"]) == (None, "未设角色", "all")

        promoted = client.patch("/api/v1/users/pmo.one", headers=admin, json={"role": "admin"}).json()["data"]
        assert promoted["is_admin"] is True and promoted["role"] == "admin"

        # R5: the project manager names to choose from.
        assert client.get("/api/v1/project-managers", headers=admin).json()["data"] == ["张三"]
        client.patch("/api/v1/users/pm.one", headers=admin, json={"is_active": False})
        assert client.get("/api/v1/project-managers", headers=admin).json()["data"] == []


def test_project_manager_sees_and_maintains_only_own_projects(tmp_path):
    with secure_client(tmp_path) as client:
        admin, pm = with_project_manager(client)
        everyone = {o["primaryKey"] for o in client.get("/api/v1/ontology/objects/Project?pageSize=200", headers=admin).json()["data"]}
        assert OWN < everyone

        # Objects, details, object sets and the business-page functions all follow the scope.
        assert {o["primaryKey"] for o in client.get("/api/v1/ontology/objects/Project?pageSize=200", headers=pm).json()["data"]} == OWN
        assert client.get("/api/v1/ontology/objects/Project/ZMJ113", headers=pm).status_code == 404
        milestones = client.get("/api/v1/ontology/objects/Milestone?pageSize=200", headers=pm).json()["data"]
        assert milestones and {m["properties"]["projectNumber"] for m in milestones} <= OWN
        loaded = client.post("/api/v1/ontology/objectSets/loadObjects", headers=pm,
                             json={"objectSet": {"type": "base", "objectType": "Project"}}).json()["data"]
        assert {o["primaryKey"] for o in loaded} == OWN
        assert project_numbers(execute(client, pm, "projectList").json()["data"]["result"]["data"]) == OWN
        assert project_numbers(execute(client, pm, "portfolioHealth").json()["data"]["result"]["data"]) == OWN
        assert execute(client, pm, "projectOverview", {"project": "ZMJ113"}).status_code in (404, 422)
        assert execute(client, pm, "projectOverview", {"project": "TPL149"}).status_code == 200
        assert client.get("/api/v1/projects", headers=pm).status_code == 200
        assert client.get("/api/v1/projects/ZMJ113", headers=pm).status_code in (404, 422)

        # Types without a project scope are not readable; neither are functions without a data scope.
        for object_type in ("Customer", "Employee", "TimeEntry", "Contract"):
            assert client.get(f"/api/v1/ontology/objects/{object_type}", headers=pm).status_code == 403, object_type
        assert execute(client, pm, "customerAnnualSummary").status_code == 403
        assert execute(client, pm, "costObjectInvestment", {"costObject": "TPL149"}).status_code == 403
        assert execute(client, pm, "workingCalendar", {"startDate": "2026-10-01", "endDate": "2026-10-31"}).status_code == 200
        schema = client.get("/api/v1/ontology/schema", headers=pm).json()["data"]
        assert {t["apiName"] for t in schema["objectTypes"]} <= {"Project", "Milestone", "PaymentNode", "PaymentTerm", "Intervention"}

        # Maintenance: own projects only.
        own = client.post("/api/v1/projects/TPL149/milestones", headers=pm, json={"name": "PM 自建", "planned_end": "2026-12-20"})
        assert own.status_code == 201, own.text
        other = client.post("/api/v1/projects/ZMJ113/milestones", headers=pm, json={"name": "越权", "planned_end": "2026-12-20"})
        assert other.status_code in (404, 422)
        sey101 = client.get("/api/v1/projects/ZMJ113", headers=admin).json()["data"]["milestones"]
        assert "越权" not in {m["name"] for m in sey101}

        # The audit log shows a project manager their own entries only.
        log = client.get("/api/v1/ontology/action-log?pageSize=100", headers=pm).json()["data"]
        assert log and {entry["actor"] for entry in log} == {"liulang"}


def test_scoped_requests_do_not_pollute_the_shared_index(tmp_path):
    with secure_client(tmp_path) as client:
        admin, pm = with_project_manager(client)

        def admin_view():
            rows = client.get("/api/v1/ontology/objects/Project?pageSize=200", headers=admin).json()["data"]
            return {o["primaryKey"]: o["properties"] for o in rows}

        client.patch("/api/v1/projects/WDR101/settings", headers=admin, json={"notes": "索引检查"})
        expected = admin_view()
        # Same write again: the index is invalidated and the project manager's request rebuilds it first.
        client.patch("/api/v1/projects/WDR101/settings", headers=admin, json={"notes": "索引检查"})
        assert len(client.get("/api/v1/ontology/objects/Project?pageSize=200", headers=pm).json()["data"]) == len(OWN)
        assert client.get("/api/v1/ontology/objects/PaymentTerm?pageSize=200", headers=pm).status_code == 200
        assert admin_view() == expected


def test_function_data_scope_is_declared():
    declared = {f.api_name: f.data_scope for f in load_ontology().functions}
    project = {"projectRiskAssessment", "projectBudgetStatus", "projectPaymentStatus", "projectHealthForPeriod",
               "portfolioSummary", "projectList", "portfolioHealth", "projectOverview", "maintenanceWorklist"}
    assert {name for name, scope in declared.items() if scope == "project"} == project
    assert {name for name, scope in declared.items() if scope == "shared"} == {"workingCalendar"}
    scoped_types = {t.api_name for t in load_ontology().object_types if t.project_scope}
    assert scoped_types == {"Project", "Milestone", "PaymentNode", "PaymentTerm", "Intervention"}


def test_base_program_endpoints_follow_the_scope(tmp_path):
    with secure_client(tmp_path) as client:
        admin, pm = with_project_manager(client)
        assert client.get("/api/v1/projects/ZMJ113/records", headers=pm).status_code == 404
        assert client.get("/api/v1/projects/TPL149/records", headers=pm).status_code in (200, 404)
        assert client.get("/api/v1/projects/TPL149/records", headers=admin).status_code == client.get(
            "/api/v1/projects/TPL149/records", headers=pm).status_code
        assert client.post("/api/v1/projects/ZMJ113/ai-analysis", headers=pm).status_code == 404
        # PM Assistant opened to project managers with G5 (its tools run in their scope); no model is configured here.
        chat = client.post("/api/v1/pm-assistant/chat", headers=pm, json={"message": "哪些项目有风险？"})
        assert chat.status_code == 503 and "尚未配置模型服务" in chat.text
        assert client.get("/api/v1/exports/latest?format=json", headers=pm).status_code == 403
        assert client.get("/api/v1/exports/latest?format=json", headers=admin).status_code == 200


def test_ontology_browser_actions_are_admin_only(tmp_path):
    with secure_client(tmp_path) as client:
        admin = login(client, "admin", "Tsnt@2026")
        assert client.post("/api/v1/users", headers=admin, json={
            "username": "pmo.one", "password": "secret-pass-1", "role": "pmo"}).status_code == 201
        pmo = login(client, "pmo.one", "secret-pass-1")
        created = client.post("/api/v1/projects/WDR101/milestones", headers=pmo, json={"name": "渠道检查", "planned_end": "2026-12-20"})
        assert created.status_code == 201, created.text
        milestone = created.json()["data"]["milestone_id"]

        def edit(headers):
            return client.post("/api/v1/ontology/actions/editMilestone/apply", headers=headers,
                               json={"parameters": {"milestone": milestone, "name": "改名"}})

        browser = edit(pmo)
        assert browser.status_code == 403 and "只有管理员" in browser.text
        assert edit({**pmo, **BUSINESS}).status_code == 200
        assert edit(admin).status_code == 200

        actions = {a["apiName"]: a for a in client.get(f"/api/v1/ontology/objects/Milestone/{milestone}/actions", headers=pmo).json()["data"]}
        assert actions["editMilestone"]["permitted"] is False
        actions = {a["apiName"]: a for a in client.get(f"/api/v1/ontology/objects/Milestone/{milestone}/actions", headers=admin).json()["data"]}
        assert actions["editMilestone"]["permitted"] is True

        log = client.get("/api/v1/ontology/action-log", headers=admin,
                         params={"objectType": "Milestone", "primaryKey": milestone}).json()["data"]
        succeeded = [(e["actor"], e["channel"]) for e in log if e["actionType"] == "editMilestone" and e["status"] == "succeeded"]
        assert sorted(succeeded) == [("admin", "ontology"), ("pmo.one", "business")]
