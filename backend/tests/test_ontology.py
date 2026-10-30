from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology import load_ontology

from test_api import settings


TIME_ENTRIES = [
    {
        "external_id": f"ONT-{index}",
        "project_number": "TPL149",
        "employee_id": employee_id,
        "employee_name": name,
        "work_date": f"2026-07-0{index}",
        "hours": "8.00",
        "approved_hours": "6.00",
        "approval_status": "approved",
        "work_content": "SRM 接口联调",
        "source_updated_at": "2026-07-09T18:00:00+08:00",
    }
    for index, (employee_id, name) in enumerate([("E001", "Zhang San"), ("E002", "Li Si"), ("E001", "Zhang San")], start=1)
]


def test_ontology_definitions_are_consistent():
    ontology = load_ontology()
    ontology.validate()
    for object_type in ontology.object_types:
        names = [
            link.api_name if forward else link.reverse_api_name
            for link, forward in ontology.links_for(object_type.api_name)
        ]
        assert len(names) == len(set(names)), object_type.api_name
        for prop in object_type.properties:
            if prop.derived_from:
                # A derived property names the derivation that computes it (or, before migration, a function).
                assert prop.derived_from in object_type.derivations or ontology.function(prop.derived_from), prop.api_name
            if object_type.derivations and prop.source_kind == "derived":
                assert prop.derived_from in object_type.derivations, prop.api_name


def test_schema_objects_links_and_functions(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.post("/api/v1/time-entries/import", json=TIME_ENTRIES).status_code == 200
        schema = client.get("/api/v1/ontology/schema").json()["data"]
        assert {t["apiName"] for t in schema["objectTypes"]} >= {
            "Project", "Contract", "Customer", "Employee", "Milestone", "PaymentNode",
        }
        project_type = next(t for t in schema["objectTypes"] if t["apiName"] == "Project")
        contract_type = next(p for p in project_type["properties"] if p["apiName"] == "contractType")
        assert contract_type["sourceKind"] == "manual"
        assert contract_type["editedBy"] == ["configureProject"]

        projects = client.get("/api/v1/ontology/objects/Project", params={"q": "TPL149"}).json()
        assert projects["meta"]["totalObjects"] == 1
        filtered = client.get(
            "/api/v1/ontology/objects/Project", params={"where.contractType": "construction"}
        ).json()
        assert all(o["properties"]["contractType"] == "construction" for o in filtered["data"])

        project = client.get("/api/v1/ontology/objects/Project/TPL149").json()["data"]
        links = {link["apiName"]: link["count"] for link in project["links"]}
        assert links["team"] == 2
        assert links["timeEntries"] == 3
        assert links["milestones"] >= 1

        team = client.get("/api/v1/ontology/objects/Project/TPL149/links/team").json()
        assert {o["primaryKey"] for o in team["data"]} == {"E001", "E002"}
        worked = client.get("/api/v1/ontology/objects/Employee/E001/links/workedProjects").json()
        assert [o["primaryKey"] for o in worked["data"]] == ["TPL149"]
        employee = client.get("/api/v1/ontology/objects/Employee/E001").json()["data"]
        assert employee["properties"]["reportedHours"] == "16.00"

        customer = project["properties"]["customerName"]
        customer_projects = client.get(
            f"/api/v1/ontology/objects/Customer/{customer}/links/projects"
        ).json()
        assert "TPL149" in {o["primaryKey"] for o in customer_projects["data"]}

        budget = client.post(
            "/api/v1/ontology/functions/projectBudgetStatus/execute",
            json={"parameters": {"project": "TPL149"}},
        ).json()["data"]["result"]
        assert budget["reportedPersonDays"] == "3.00"
        assert budget["confirmationGapPersonDays"] == "0.75"

        missing = client.post(
            "/api/v1/ontology/functions/projectBudgetStatus/execute",
            json={"parameters": {"project": "NOPE"}},
        )
        assert missing.status_code == 422
        assert client.get("/api/v1/ontology/objects/Unknown").status_code == 404


def test_actions_validate_write_back_and_audit(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        dry_run = client.post(
            "/api/v1/ontology/actions/configureProject/apply",
            json={
                "parameters": {"project": "TPL149", "contractType": "maintenance", "plannedPersonDays": None},
                "validateOnly": True,
            },
        ).json()["data"]
        assert dry_run["valid"] is False
        assert dry_run["criteria"][0]["passed"] is False

        rejected = client.post(
            "/api/v1/ontology/actions/configureProject/apply",
            json={"parameters": {"project": "TPL149", "plannedStart": "2026-09-01", "plannedEnd": "2026-08-01"}},
        )
        assert rejected.status_code == 422
        assert rejected.json()["code"] == "ONTOLOGY_ACTION_REJECTED"

        bad_param = client.post(
            "/api/v1/ontology/actions/configureProject/apply",
            json={"parameters": {"project": "TPL149", "contractType": "space"}},
        )
        assert bad_param.status_code == 422
        assert bad_param.json()["errors"]["parameterErrors"][0]["parameter"] == "contractType"

        applied = client.post(
            "/api/v1/ontology/actions/configureProject/apply",
            json={"parameters": {"project": "TPL149", "projectManager": "王经理", "plannedPersonDays": "40"}},
        ).json()["data"]
        assert applied["status"] == "succeeded"
        assert applied["objects"][0]["properties"]["projectManager"] == "王经理"
        assert applied["objects"][0]["properties"]["plannedPersonDays"] == "40.00"

        term = client.post("/api/v1/projects/TPL149/payment-terms", json={"name": "验收款", "planned_amount": "5000"}).json()["data"]
        created = client.post(
            "/api/v1/ontology/actions/createMilestone/apply",
            json={"parameters": {
                "project": "TPL149", "name": "验收", "plannedEnd": "2026-12-01", "paymentTermId": term["payment_term_id"],
            }},
        ).json()["data"]
        milestone_id = next(e["primaryKey"] for e in created["edits"] if e["objectType"] == "Milestone")
        payment_id = next(e["primaryKey"] for e in created["edits"] if e["objectType"] == "PaymentNode")

        linked = client.get(f"/api/v1/ontology/objects/Milestone/{milestone_id}/links/paymentNode").json()
        assert [o["primaryKey"] for o in linked["data"]] == [payment_id]

        delete_linked = client.post(
            "/api/v1/ontology/actions/deletePaymentNode/apply",
            json={"parameters": {"paymentNode": payment_id}},
        )
        assert delete_linked.status_code == 422

        confirmed = client.post(
            "/api/v1/ontology/actions/confirmMilestoneCompletion/apply",
            json={"parameters": {"milestone": milestone_id}},
        ).json()["data"]
        assert confirmed["objects"][0]["properties"]["status"] == "completed"
        assert confirmed["objects"][0]["properties"]["confirmedBy"] == "test"

        actions = client.get(f"/api/v1/ontology/objects/Milestone/{milestone_id}/actions").json()["data"]
        edit = next(a for a in actions if a["apiName"] == "editMilestone")
        assert edit["available"] is False

        locked = client.post(
            "/api/v1/ontology/actions/editMilestone/apply",
            json={"parameters": {"milestone": milestone_id, "name": "改名"}},
        )
        assert locked.status_code == 422

        intervention = client.post(
            "/api/v1/ontology/actions/openIntervention/apply",
            json={"parameters": {"project": "TPL149", "title": "补充资源"}},
        ).json()["data"]["edits"][0]["primaryKey"]
        closed = client.post(
            "/api/v1/ontology/actions/updateInterventionStatus/apply",
            json={"parameters": {"intervention": intervention, "status": "closed"}},
        )
        assert closed.status_code == 200
        reopen = client.post(
            "/api/v1/ontology/actions/updateInterventionStatus/apply",
            json={"parameters": {"intervention": intervention, "status": "open"}},
        )
        assert reopen.status_code == 422

        legacy = client.patch("/api/v1/projects/TPL149/settings", json={"notes": "legacy"})
        assert legacy.status_code == 200

        log = client.get("/api/v1/ontology/action-log", params={"objectType": "Project", "primaryKey": "TPL149"}).json()
        channels = {entry["channel"] for entry in log["data"]}
        statuses = {entry["status"] for entry in log["data"]}
        assert channels == {"ontology", "business"}, "maintenance endpoints run ontology actions (business channel); no legacy path"
        assert {"succeeded", "rejected"} <= statuses
        milestone_log = client.get(
            "/api/v1/ontology/action-log", params={"primaryKey": milestone_id}
        ).json()["data"]
        assert {entry["actionType"] for entry in milestone_log} >= {
            "createMilestone", "confirmMilestoneCompletion", "editMilestone",
        }


def test_safe_policies_redact_markings_and_guard_actions(tmp_path):
    secure = replace(settings(tmp_path), auth_enabled=True)
    with TestClient(create_app(secure)) as client:
        admin_token = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Tsnt@2026"}
        ).json()["data"]["token"]
        admin = {"Authorization": f"Bearer {admin_token}"}
        assert client.post("/api/v1/time-entries/import", headers=admin, json=TIME_ENTRIES).status_code == 200

        def user(name: str, permissions: list[str]) -> dict[str, str]:
            client.post(
                "/api/v1/users", headers=admin,
                json={"username": name, "password": f"{name}-pass-1", "permissions": permissions},
            )
            token = client.post(
                "/api/v1/auth/login", json={"username": name, "password": f"{name}-pass-1"}
            ).json()["data"]["token"]
            return {"Authorization": f"Bearer {token}"}

        health = user("healthonly", ["health"])
        project = client.get("/api/v1/ontology/objects/Project/TPL149", headers=health).json()["data"]
        assert project["properties"]["contractValue"] is None
        assert "contractValue" in project["redactedProperties"]
        assert project["properties"]["riskLevel"]
        assert "milestones" not in {link["apiName"] for link in project["links"]}
        assert client.get("/api/v1/ontology/objects/Milestone", headers=health).status_code == 403
        assert client.get(
            "/api/v1/ontology/objects/Project", headers=health, params={"orderBy": "contractValue"}
        ).status_code == 403
        assert client.post(
            "/api/v1/ontology/actions/configureProject/apply", headers=health,
            json={"parameters": {"project": "TPL149", "notes": "x"}},
        ).status_code == 403
        assert client.post(
            "/api/v1/ontology/functions/projectPaymentStatus/execute", headers=health,
            json={"parameters": {"project": "TPL149"}},
        ).status_code == 403
        schema = client.get("/api/v1/ontology/schema", headers=health).json()["data"]
        assert "Milestone" not in {t["apiName"] for t in schema["objectTypes"]}
        assert not next(m for m in schema["markings"] if m["apiName"] == "FINANCE")["granted"]

        manager = user("manager", ["projects"])
        entry = client.get("/api/v1/ontology/objects/TimeEntry/ONT-1", headers=manager).json()["data"]
        assert entry["properties"]["hours"] is None
        assert entry["properties"]["workContent"] == "SRM 接口联调"
        project = client.get("/api/v1/ontology/objects/Project/TPL149", headers=manager).json()["data"]
        assert project["properties"]["contractValue"] is not None

        people = user("hrteam", ["employeeWorktime"])
        assert client.get("/api/v1/ontology/objects/Project", headers=people).status_code == 403
        employee = client.get("/api/v1/ontology/objects/Employee/E001", headers=people).json()["data"]
        assert employee["properties"]["reportedHours"] == "16.00"
        assert "workedProjects" not in {link["apiName"] for link in employee["links"]}
