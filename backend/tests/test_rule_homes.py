"""Rule homes and declared linkages (ontology 7.5.0, build package docs/ontology-builds/2026-10-29-rule-homes-and-linkages.yaml).

Every manual-maintenance rule has one implementation (app/maintenance_rules.py) that the action criteria and the
base program's guards share; every action writes only the datasets it declares.
"""
import inspect

import pytest
from fastapi.testclient import TestClient

from app import project_repository
from app.main import create_app
from app.ontology import load_ontology

from test_api import settings


def apply(client, action, parameters, *, validate_only=False):
    suffix = "?validateOnly=true" if validate_only else ""
    return client.post(f"/api/v1/ontology/actions/{action}/apply{suffix}", json={"parameters": parameters})


def created(response, object_type):
    assert response.status_code == 200, response.text
    return next(e["primaryKey"] for e in response.json()["data"]["edits"] if e["objectType"] == object_type)


def rejected(response, reason):
    assert response.status_code == 422, response.text
    assert reason in response.text, response.text


def base_refuses(call, reason):
    with pytest.raises(ValueError) as error:
        call()
    assert str(error.value) == reason


def test_rules_have_one_implementation_for_actions_and_base_program(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        base = client.app.state.project_repository

        # Object rules: date order and the budget of capped project types.
        rejected(apply(client, "configureProject", {"project": "WDR101", "plannedStart": "2026-12-01", "plannedEnd": "2026-11-01"}),
                 "计划结束日期不能早于开始日期")
        base_refuses(lambda: base.upsert_settings("WDR101", {"planned_start": "2026-12-01", "planned_end": "2026-11-01"}),
                     "计划结束日期不能早于开始日期")
        rejected(apply(client, "configureProject", {"project": "WDR101", "contractType": "construction", "plannedPersonDays": None}),
                 "系统实施、TSnT365 项目必须填写预算人天")
        base_refuses(lambda: base.upsert_settings("WDR101", {"contract_type": "construction", "planned_person_days": None}),
                     "系统实施、TSnT365 项目必须填写预算人天")

        # Locks share one table: a completed milestone.
        done = created(apply(client, "createMilestone", {"project": "WDR101", "name": "启动", "plannedEnd": "2026-11-30"}), "Milestone")
        assert apply(client, "confirmMilestoneCompletion", {"milestone": done}).status_code == 200
        rejected(apply(client, "editMilestone", {"milestone": done, "name": "改名"}), "里程碑已确认完成，已锁定")
        base_refuses(lambda: base.update_milestone("WDR101", done, {"name": "改名"}), "里程碑已确认完成，已锁定")

        # R2: the payment ratio and one-milestone-per-term rules are now visible to the action (already in a dry run).
        rejected(apply(client, "createPaymentTerm", {"project": "WDR101", "name": "首付款", "plannedAmount": "1000", "paymentRatio": "120"},
                       validate_only=True), "回款比例必须在 0 到 100 之间")
        base_refuses(lambda: base.create_payment_term("WDR101", {"name": "首付款", "planned_amount": "1000", "payment_ratio": "120"}),
                     "回款比例必须在 0 到 100 之间")
        term = created(apply(client, "createPaymentTerm", {"project": "WDR101", "name": "首付款", "plannedAmount": "1000"}), "PaymentTerm")
        first = created(apply(client, "createMilestone", {"project": "WDR101", "name": "签约", "plannedEnd": "2026-12-15",
                                                          "paymentTermId": term}), "Milestone")
        rejected(apply(client, "createMilestone", {"project": "WDR101", "name": "验收", "plannedEnd": "2026-12-20", "paymentTermId": term},
                       validate_only=True), "该回款约定已关联其他里程碑")
        base_refuses(lambda: base.create_milestone("WDR101", {"name": "验收", "planned_end": "2026-12-20", "payment_term_id": term}),
                     "该回款约定已关联其他里程碑，请先在原里程碑上解除关联")
        assert apply(client, "editMilestone", {"milestone": first, "paymentTermId": term}, validate_only=True).status_code == 200, \
            "relinking the term to its own milestone is fine"

        # A received payment locks the node and its payment term in both layers.
        node = next(m["payment_node_id"] for m in client.get("/api/v1/projects/WDR101").json()["data"]["milestones"] if m["milestone_id"] == first)
        assert apply(client, "confirmPaymentReceived", {"paymentNode": node, "actualAmount": "1000"}).status_code == 200
        rejected(apply(client, "editPaymentNode", {"paymentNode": node, "plannedAmount": "900"}), "回款已确认到账，已锁定")
        base_refuses(lambda: base.update_payment_node("WDR101", node, {"planned_amount": "900"}), "回款已确认到账，已锁定")
        rejected(apply(client, "editPaymentTerm", {"paymentTerm": term, "name": "首付款（改）"}), "回款约定已确认到账，已锁定")
        base_refuses(lambda: base.update_payment_term("WDR101", term, {"name": "首付款（改）"}), "回款约定已确认到账，已锁定")

    source = inspect.getsource(project_repository)
    for english in ("cannot be edited", "cannot be deleted", "cannot be changed", "cannot be removed", "cannot be reopened",
                    "must be non-negative", "is required for capped", "already linked to another milestone"):
        assert english not in source, f"the repository still has its own wording: {english}"


def test_actions_write_only_declared_datasets(tmp_path):
    """Run every action once and compare the dataset versions before and after with its declared writeback."""
    ontology = load_ontology()
    backing = {t.api_name: set(t.backing_datasets) for t in ontology.object_types}
    exercised: dict[str, set[str]] = {}

    with TestClient(create_app(settings(tmp_path))) as client:
        database = client.app.state.database
        base = client.app.state.project_repository

        def run(action, parameters, object_type=None):
            before = database.dataset_versions()
            response = apply(client, action, parameters)
            assert response.status_code == 200, (action, response.text)
            after = database.dataset_versions()
            exercised[action] = {name for name, version in after.items() if version != before.get(name)}
            return created(response, object_type) if object_type else None

        run("configureProject", {"project": "WDR101", "projectManager": "王经理"})
        term = run("createPaymentTerm", {"project": "WDR101", "name": "首付款", "plannedAmount": "1000"}, "PaymentTerm")
        milestone = run("createMilestone", {"project": "WDR101", "name": "签约", "plannedEnd": "2026-12-15", "paymentTermId": term}, "Milestone")
        run("editMilestone", {"milestone": milestone, "plannedEnd": "2026-12-20"})
        run("editPaymentTerm", {"paymentTerm": term, "plannedAmount": "1500"})
        run("deleteMilestone", {"milestone": milestone})
        run("deletePaymentTerm", {"paymentTerm": term})
        done = run("createMilestone", {"project": "WDR101", "name": "启动", "plannedEnd": "2026-11-30"}, "Milestone")
        run("confirmMilestoneCompletion", {"milestone": done})

        seeded = {n["name"]: n["payment_node_id"] for n in client.get("/api/v1/projects/TPL149").json()["data"]["payment_nodes"]}
        run("editPaymentNode", {"paymentNode": seeded["首期款"], "plannedAmount": "74000"})
        run("confirmPaymentReceived", {"paymentNode": seeded["验收尾款"], "actualAmount": "1000"})
        run("deletePaymentNode", {"paymentNode": seeded["首期款"]})

        intervention = run("openIntervention", {"project": "WDR101", "title": "补充资源", "dueDate": "2026-12-31"}, "Intervention")
        run("updateInterventionStatus", {"intervention": intervention, "status": "closed"})
        run("recordOpportunityOutcome", {"costObject": "WDR101", "outcome": "lost", "reason": "客户取消"})
        run("reopenOpportunity", {"costObject": "WDR101", "reason": "客户重新启动"})
        run("updateOperatingPolicy", {"policy": "default", "budgetAttentionPercent": "80"})
        run("setAnnualOperatingPlan", {"policy": "default", "year": 2026})
        run("recordOperatingCost", {"policy": "default", "month": "2026-01", "laborCost": "1000"})

    assert set(exercised) == {a.api_name for a in ontology.action_types}, "every action is exercised"
    for action in ontology.action_types:
        written = exercised[action.api_name]
        undeclared = written - set(action.writeback_datasets)
        assert not undeclared, f"{action.api_name} writes {sorted(undeclared)} without declaring it in writeback"
        edited_types = {edit.split(".")[0] for edit in action.edits}
        for dataset in written:
            assert any(dataset in backing.get(t, set()) for t in edited_types), \
                f"{action.api_name} writes {dataset} but none of its edited object types {sorted(edited_types)} is backed by it"
