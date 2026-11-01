"""Management interventions as a working loop (ontology 8.0.0, build package docs/ontology-builds/2026-11-01-intervention-loop.yaml).

Raised and moved forward from the project drawer by whoever maintains the project (D31); an open intervention past its
due date is a to-do reminder (D32) that does not change the risk level (D33); the due date is required, the owner
defaults to the project manager and the type is a fixed list (D34). Dates are far in the past or future so the tests
do not depend on the day they run.
"""
from fastapi.testclient import TestClient

from app.main import create_app

from test_api import settings
from test_roles_and_scope import login, secure_client, with_project_manager

PAST, FUTURE = "2000-01-01", "2999-12-31"


def open_one(client, project="WDR101", headers=None, **fields):
    return client.post(f"/api/v1/projects/{project}/interventions", headers=headers or {},
                       json={"title": "补充测试资源", **fields})


def project(client, code="WDR101"):
    return client.get(f"/api/v1/projects/{code}").json()["data"]


def test_raising_needs_a_due_date_and_fills_the_owner(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.patch("/api/v1/projects/WDR101/settings", json={"project_manager": "刘浪"}).status_code == 200
        missing = open_one(client)
        assert missing.status_code == 422 and "截止日期" in missing.text

        created = open_one(client, due_date=FUTURE, intervention_type="resource")
        assert created.status_code == 201, created.text
        row = created.json()["data"]
        assert (row["owner"], row["intervention_type"], row["status"]) == ("刘浪", "resource", "open"), "owner defaults to the PM"
        named = open_one(client, due_date=FUTURE, owner="王总").json()["data"]
        assert (named["owner"], named["intervention_type"]) == ("王总", "management"), "a named owner wins; type defaults to 其他"

        assert open_one(client, due_date=FUTURE, intervention_type="politics").status_code == 422, "type is a fixed list"
        schema = {v["apiName"]: v for v in client.get("/api/v1/ontology/schema").json()["data"]["valueTypes"]}
        labels = {v["value"]: v["label"] for v in schema["InterventionType"]["values"]}
        assert labels == {"schedule": "进度", "resource": "资源", "payment": "回款", "customer": "客户", "quality": "质量", "management": "其他"}


def test_overdue_interventions_are_reminders_not_risk(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        before = project(client)
        late = open_one(client, due_date=PAST, owner="王总").json()["data"]
        open_one(client, due_date=FUTURE)
        closed = open_one(client, due_date=PAST).json()["data"]
        assert client.post(f"/api/v1/projects/WDR101/interventions/{closed['intervention_id']}/status",
                           json={"status": "closed"}).status_code == 200

        after = project(client)
        assert (after["open_interventions"], after["overdue_interventions"]) == (2, 1)
        [alert] = [a for a in after["anomaly_alerts"] if a["code"] == "INTERVENTION_OVERDUE"]
        assert alert["severity"] == "warning" and alert["title"] == "管理干预逾期 · 补充测试资源" and "王总" in alert["detail"]
        assert (after["risk_level"], after["risk_reasons"]) == (before["risk_level"], before["risk_reasons"]), "D33"

        objects = {o["primaryKey"]: o["properties"] for o in client.get("/api/v1/ontology/objects/Intervention").json()["data"]}
        assert objects[late["intervention_id"]]["overdue"] is True
        assert objects[closed["intervention_id"]]["overdue"] is False, "closed is never overdue"
        assert client.get("/api/v1/ontology/objects/Project/WDR101").json()["data"]["properties"]["overdueInterventions"] == 1


def test_drawer_endpoints_follow_project_maintenance_rights(tmp_path):
    with secure_client(tmp_path) as client:
        admin, pm = with_project_manager(client)
        own = open_one(client, "TPL149", pm, due_date=FUTURE)
        assert own.status_code == 201, "a project manager raises interventions on own projects"
        assert open_one(client, "ZMJ113", pm, due_date=FUTURE).status_code == 404, "not on other projects"

        status = f"/api/v1/projects/TPL149/interventions/{own.json()['data']['intervention_id']}/status"
        assert client.post(status, headers=pm, json={"status": "in_progress", "notes": "已协调"}).status_code == 200
        assert client.post("/api/v1/projects/WDR101/interventions/" + own.json()["data"]["intervention_id"] + "/status",
                           headers=admin, json={"status": "closed"}).status_code == 404, "the id must belong to the project"

        assert client.post("/api/v1/users", headers=admin, json={
            "username": "dept.head", "password": "secret-pass-1", "role": "department_head"}).status_code == 201
        head = login(client, "dept.head", "secret-pass-1")
        assert open_one(client, "TPL149", head, due_date=FUTURE).status_code == 403, "department heads only read"
        overview = client.post("/api/v1/ontology/functions/projectOverview/execute", headers=head,
                               json={"parameters": {"project": "TPL149"}})
        assert overview.status_code == 403, "like milestones and payments: no project drawer without the maintenance menu"

        assert client.post(status, headers=pm, json={"status": "closed"}).status_code == 200
        reopened = client.post(status, headers=admin, json={"status": "open"})
        assert reopened.status_code == 422 and "已关闭" in reopened.text, "closed interventions stay locked"
