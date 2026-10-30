"""Logical delete (ontology 7.6.0, build package docs/ontology-builds/2026-10-29-logical-delete.yaml).

Deleting a milestone, payment term or payment node marks the row and keeps it (requirements baseline D6); a marked
row takes part in no read. Interventions cannot be deleted at all, only closed.
"""
from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology import load_ontology

from test_api import settings
from test_rule_homes import apply, created


def stored(database, table, column, value):
    with database.connect() as connection:
        rows = connection.execute(f"SELECT * FROM {table} WHERE {column} = ?", (value,)).fetchall()
    return [dict(row) for row in rows]


def facts(client, project):
    return client.get(f"/api/v1/ontology/objects/Project/{project}").json()["data"]["properties"]


def listed(client, object_type, project):
    objects = client.get(f"/api/v1/ontology/objects/{object_type}?pageSize=200").json()["data"]
    return {o["primaryKey"] for o in objects if o["properties"].get("projectNumber") == project}


def test_deleted_records_are_kept_and_hidden(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        database = client.app.state.database
        before = facts(client, "WDR101")

        # R1: a milestone with a payment term -> the milestone and its node are marked, the term is unlinked and kept.
        term = created(apply(client, "createPaymentTerm", {"project": "WDR101", "name": "首付款", "plannedAmount": "1000"}), "PaymentTerm")
        milestone = created(apply(client, "createMilestone", {"project": "WDR101", "name": "签约", "plannedEnd": "2026-12-15",
                                                              "paymentTermId": term}), "Milestone")
        node = stored(database, "milestones", "milestone_id", milestone)[0]["payment_node_id"]
        assert node in listed(client, "PaymentNode", "WDR101")
        assert facts(client, "WDR101")["paymentNodeTotal"] == before["paymentNodeTotal"] + 1

        assert apply(client, "deleteMilestone", {"milestone": milestone}).status_code == 200
        [kept] = stored(database, "milestones", "milestone_id", milestone)
        [kept_node] = stored(database, "payment_nodes", "payment_node_id", node)
        [kept_term] = stored(database, "payment_terms", "payment_term_id", term)
        actor = stored(database, "ontology_action_log", "action_type", "deleteMilestone")[0]["actor"]
        assert kept["deleted_at"] and kept["deleted_by"] == actor
        assert kept_node["deleted_at"] and kept_node["deleted_by"] == actor and kept_node["payment_term_id"] == term
        assert kept_term["deleted_at"] is None and kept_term["linked_milestone_id"] is None and kept_term["payment_node_id"] is None

        # R2: hidden from objects, project detail and derived counts; acting on it is "not found".
        assert milestone not in listed(client, "Milestone", "WDR101") and node not in listed(client, "PaymentNode", "WDR101")
        assert client.get(f"/api/v1/ontology/objects/Milestone/{milestone}").status_code == 404
        assert client.get(f"/api/v1/ontology/objects/PaymentNode/{node}").status_code == 404
        detail = client.get("/api/v1/projects/WDR101").json()["data"]
        assert milestone not in {m["milestone_id"] for m in detail["milestones"]}
        assert node not in {n["payment_node_id"] for n in detail["payment_nodes"]}
        after = facts(client, "WDR101")
        assert (after["milestoneTotal"], after["paymentNodeTotal"]) == (before["milestoneTotal"], before["paymentNodeTotal"])
        for action, parameters in (("editMilestone", {"milestone": milestone, "name": "改名"}),
                                   ("confirmMilestoneCompletion", {"milestone": milestone}),
                                   ("deleteMilestone", {"milestone": milestone})):
            assert apply(client, action, parameters).status_code in (404, 422), action
        base = client.app.state.project_repository
        for call in (lambda: base.update_milestone("WDR101", milestone, {"name": "改名"}),
                     lambda: base.confirm_milestone("WDR101", milestone, "测试"),
                     lambda: base.delete_milestone("WDR101", milestone)):
            try:
                call()
            except KeyError:
                pass
            else:
                raise AssertionError("the base program acted on a deleted milestone")

        # The default sequence number counts live milestones only.
        last = created(apply(client, "createMilestone", {"project": "WDR101", "name": "末项", "plannedEnd": "2026-12-20"}), "Milestone")
        number = stored(database, "milestones", "milestone_id", last)[0]["sequence_no"]
        assert apply(client, "deleteMilestone", {"milestone": last}).status_code == 200
        again = created(apply(client, "createMilestone", {"project": "WDR101", "name": "末项", "plannedEnd": "2026-12-20"}), "Milestone")
        assert stored(database, "milestones", "milestone_id", again)[0]["sequence_no"] == number

        # R1 + R2 for a payment term and an independent payment node.
        assert apply(client, "deletePaymentTerm", {"paymentTerm": term}).status_code == 200
        assert stored(database, "payment_terms", "payment_term_id", term)[0]["deleted_by"] == actor
        assert term not in listed(client, "PaymentTerm", "WDR101")
        assert facts(client, "WDR101")["paymentTermCount"] == before["paymentTermCount"]
        assert apply(client, "createMilestone", {"project": "WDR101", "name": "验收", "plannedEnd": "2026-12-30", "paymentTermId": term},
                     validate_only=True).status_code in (404, 422), "a deleted term cannot be linked"

        seeded = {n["name"]: n["payment_node_id"] for n in client.get("/api/v1/projects/TPL149").json()["data"]["payment_nodes"]}
        nodes_before = facts(client, "TPL149")["paymentNodeTotal"]
        assert apply(client, "deletePaymentNode", {"paymentNode": seeded["首期款"]}).status_code == 200
        assert stored(database, "payment_nodes", "payment_node_id", seeded["首期款"])[0]["deleted_at"]
        assert facts(client, "TPL149")["paymentNodeTotal"] == nodes_before - 1
        assert apply(client, "editPaymentNode", {"paymentNode": seeded["首期款"], "plannedAmount": "1"}).status_code in (404, 422)

        audited = {row["action_type"] for row in stored(database, "ontology_action_log", "status", "succeeded")}
        assert {"deleteMilestone", "deletePaymentTerm", "deletePaymentNode"} <= audited


def test_a_term_can_be_relinked_after_its_milestone_is_deleted(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        database = client.app.state.database
        term = created(apply(client, "createPaymentTerm", {"project": "WDR101", "name": "验收款", "plannedAmount": "2000"}), "PaymentTerm")
        first = created(apply(client, "createMilestone", {"project": "WDR101", "name": "初验", "plannedEnd": "2026-12-15",
                                                          "paymentTermId": term}), "Milestone")
        assert apply(client, "deleteMilestone", {"milestone": first}).status_code == 200
        second = created(apply(client, "createMilestone", {"project": "WDR101", "name": "终验", "plannedEnd": "2027-01-15",
                                                           "paymentTermId": term}), "Milestone")

        nodes = stored(database, "payment_nodes", "payment_term_id", term)
        assert len(nodes) == 2
        live = [n for n in nodes if n["deleted_at"] is None]
        assert len(live) == 1 and live[0]["planned_date"] == "2027-01-15"
        assert stored(database, "payment_terms", "payment_term_id", term)[0]["linked_milestone_id"] == second

        # Editing the milestone away from the term retires the node the same way.
        assert apply(client, "editMilestone", {"milestone": second, "paymentTermId": None}).status_code == 200
        assert all(n["deleted_at"] for n in stored(database, "payment_nodes", "payment_term_id", term))


def test_interventions_cannot_be_deleted():
    ontology = load_ontology()
    # Actions that change interventions: open one (on a project) and update its status (closing locks it).
    on_interventions = {a.api_name for a in ontology.action_types if any(e.split(".")[0] == "Intervention" for e in a.edits)}
    assert on_interventions == {"openIntervention", "updateInterventionStatus"}
    assert not any(a.api_name.lower().startswith("delete") and "intervention" in a.api_name.lower() for a in ontology.action_types)
