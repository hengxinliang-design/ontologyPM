"""Open every object type the way the ontology browser does: detail, each link and the available actions.

Criteria run for the action list with only the target parameter; a criterion that assumed a parameter crashed the
运营口径参数 detail with a 500 (annualPlanValuesValid read values["year"], fixed in v1.27.1). This sweep keeps any
object type's detail from failing that way again.
"""
from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology import load_ontology

from test_api import settings
from test_pmo_economics import entry, import_entries


def apply(client, action, parameters):
    response = client.post(f"/api/v1/ontology/actions/{action}/apply", json={"parameters": parameters})
    assert response.status_code == 200, (action, response.text)


def test_every_object_type_opens_with_links_and_actions(tmp_path):
    ontology = load_ontology()
    with TestClient(create_app(settings(tmp_path))) as client:
        # Objects the example data does not have: maintain one of each through its action.
        apply(client, "setAnnualOperatingPlan", {"policy": "default", "year": 2026, "collectionTarget": "1000000"})
        apply(client, "recordOperatingCost", {"policy": "default", "month": "2026-01", "laborCost": "1000"})
        apply(client, "openIntervention", {"project": "WDR101", "title": "补充资源", "dueDate": "2026-12-31"})
        apply(client, "createPaymentTerm", {"project": "WDR101", "name": "首付款", "plannedAmount": "1000"})
        import_entries(client, [entry("S1", "WDR101", "2026-10-12")])

        opened = set()
        for definition in ontology.object_types:
            rows = client.get(f"/api/v1/ontology/objects/{definition.api_name}?pageSize=1").json()["data"]
            if not rows:
                continue
            key = rows[0]["primaryKey"]
            base = f"/api/v1/ontology/objects/{definition.api_name}/{key}"
            assert client.get(base).status_code == 200, (definition.api_name, key)
            actions = client.get(f"{base}/actions")
            assert actions.status_code == 200, (definition.api_name, actions.text)
            for link, forward in ontology.links_for(definition.api_name):
                name = link.api_name if forward else link.reverse_api_name
                response = client.get(f"{base}/links/{name}")
                assert response.status_code == 200, (definition.api_name, name, response.text)
            opened.add(definition.api_name)
        assert opened == {t.api_name for t in ontology.object_types}, "every object type was opened"


def test_action_list_does_not_invent_missing_parameter_errors(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        actions = {a["apiName"]: a for a in client.get("/api/v1/ontology/objects/OperatingPolicy/default/actions").json()["data"]}
        for name in ("setAnnualOperatingPlan", "recordOperatingCost"):
            failed = [c["reason"] for c in actions[name]["criteria"] if not c["passed"]]
            assert failed == [], (name, failed)


def test_object_list_shows_referenced_titles(tmp_path):
    """User 2026-11-01: the 回款约定 list showed ms_… for 关联里程碑; the list now carries the milestone's name."""
    with TestClient(create_app(settings(tmp_path))) as client:
        term = client.post("/api/v1/projects/WDR101/payment-terms", json={"name": "首付款", "planned_amount": "1000"}).json()["data"]
        milestone = client.post("/api/v1/projects/WDR101/milestones", json={
            "name": "项目启动", "planned_end": "2026-12-01", "payment_term_id": term["payment_term_id"]}).json()["data"]
        [row] = client.get("/api/v1/ontology/objects/PaymentTerm").json()["data"]
        assert row["properties"]["linkedMilestoneId"] == milestone["milestone_id"]
        assert row["references"]["linkedMilestoneId"] == {
            "objectType": "Milestone", "primaryKey": milestone["milestone_id"], "title": "项目启动"}
        milestones = {r["primaryKey"]: r for r in client.get("/api/v1/ontology/objects/Milestone").json()["data"]}
        assert milestones[milestone["milestone_id"]]["references"]["paymentNodeId"]["title"] == "首付款"
