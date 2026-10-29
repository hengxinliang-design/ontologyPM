"""Maintenance in the ontology (ontology 4.2.0, build package docs/ontology-builds/2026-10-27-ontology-maintenance.yaml).

What people must supplement after import is declared in the metadata; the ontology derives what is maintained and
what is still missing, and every maintenance write runs an ontology action (the REST endpoints only wrap them).
"""
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology.compiler import DEFAULT_METADATA_DIR, OntologyMetadataError, load_ontology

from test_api import settings


def execute(client, function, parameters):
    response = client.post(f"/api/v1/ontology/functions/{function}/execute", json={"parameters": parameters})
    assert response.status_code == 200, response.text
    return response.json()["data"]["result"]


def project(client, code):
    return client.get(f"/api/v1/ontology/objects/Project/{code}").json()["data"]["properties"]


def labels(checklist, met):
    return [entry["label"] for entry in checklist if entry["met"] is met]


def test_maintenance_requirements_are_declared_in_metadata():
    requirements = load_ontology().object_type("Project").maintenance_requirements
    assert [(r.api_name, r.display_name, r.action) for r in requirements] == [
        ("setContractType", "选择项目类型", "configureProject"),
        ("setPlannedPersonDays", "填写初始预算人天", "configureProject"),
        ("assignProjectManager", "指定项目经理", "configureProject"),
        ("setPlannedStart", "填写计划开始日期", "configureProject"),
        ("setPlannedEnd", "填写计划结束日期", "configureProject"),
        ("planMilestones", "制定里程碑计划", "createMilestone"),
    ]
    term = load_ontology().object_type("PaymentTerm").maintenance_requirements
    assert [(r.api_name, r.display_name, r.property, r.action) for r in term] == [
        ("linkMilestone", "关联里程碑", "linkedMilestoneId", "editMilestone")]
    assert requirements[1].conditions == (("contractType", ("construction", "maintenance"), True),)
    exempt = (("contractType", ("sales",), False), ("lifecycleStatus", ("completed", "closed"), False))
    assert all(r.conditions == exempt for r in requirements[2:5]), "PM and planned dates: not pre-sales, not ended/closed"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("property: contractType, missingValues: [unknown]", "property: riskLevel, missingValues: [unknown]", "只有人工维护的属性才能作为维护要求"),
        ("link: milestones, minCount: 1, action: createMilestone", "link: milestones, minCount: 1, action: configureProject", "不会创建或修改 Milestone"),
        ("property: plannedPersonDays, when:", "property: notThere, when:", "属性 'notThere' 不存在"),
        ("derivations: [maintenanceStatus, projectFacts, projectInvestment]", "derivations: [projectFacts, projectInvestment]", "必须在 derivations 中登记 maintenanceStatus"),
        ("{property: lifecycleStatus, notIn: [completed, closed]}], action: configureProject}\n    - {apiName: setPlannedStart",
         "{property: lifecycleStatus, notIn: [finished]}], action: configureProject}\n    - {apiName: setPlannedStart", "取值 \\['finished'\\] 不在 LifecycleStatus 中"),
        ("{property: contractType, notIn: [sales]}, {property: lifecycleStatus, notIn: [completed, closed]}], action: configureProject}\n    - {apiName: setPlannedStart",
         "{property: contractType, in: [sales], notIn: [sales]}], action: configureProject}\n    - {apiName: setPlannedStart", "必须且只能写 in 或 notIn 之一"),
    ],
)
def test_invalid_maintenance_requirements_are_rejected(tmp_path, old, new, message):
    metadata = tmp_path / "metadata"
    shutil.copytree(DEFAULT_METADATA_DIR, metadata)
    path = metadata / "object-types/project.yaml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(OntologyMetadataError, match=message):
        load_ontology(metadata)


def test_maintenance_status_is_derived_and_queryable(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        before = project(client, "WDR101")
        assert before["maintenanceComplete"] is False and before["maintenanceGapCount"] >= 1
        assert "制定里程碑计划" in labels(before["maintenanceChecklist"], False)

        pending = client.post("/api/v1/ontology/objectSets/loadObjects", json={"objectSet": {
            "type": "filter", "objectSet": {"type": "base", "objectType": "Project"},
            "where": {"type": "eq", "field": "maintenanceComplete", "value": False}}}).json()["data"]
        assert "WDR101" in {o["primaryKey"] for o in pending}

        worklist = execute(client, "maintenanceWorklist", {"requirement": "planMilestones"})
        milestones = next(s for s in worklist["summary"] if s["requirement"] == "planMilestones")
        assert milestones["missing"] >= 1 and milestones["applicable"] == milestones["missing"] + milestones["maintained"]
        assert any(row["primaryKey"] == "WDR101" and row["pending"] == ["制定里程碑计划"] for row in worklist["data"])

        # Maintaining through the ontology actions clears the gaps.
        configured = client.post("/api/v1/ontology/actions/configureProject/apply", json={"parameters": {
            "project": "WDR101", "contractType": "construction", "plannedPersonDays": "40", "projectManager": "王经理",
            "plannedStart": "2026-09-01", "plannedEnd": "2026-12-31"}})
        assert configured.status_code == 200, configured.text
        assert {"填写初始预算人天", "指定项目经理", "填写计划开始日期", "填写计划结束日期"} <= set(labels(project(client, "WDR101")["maintenanceChecklist"], True))
        created = client.post("/api/v1/ontology/actions/createMilestone/apply", json={"parameters": {
            "project": "WDR101", "name": "上线", "plannedEnd": "2026-12-31"}})
        assert created.status_code == 200, created.text
        after = project(client, "WDR101")
        assert after["maintenanceComplete"] is True and after["maintenanceGapCount"] == 0
        assert labels(after["maintenanceChecklist"], False) == []

        health = execute(client, "portfolioHealth", {"maintenance": "complete", "month": "2026-07"})
        assert "WDR101" in {row["project_number"] for row in health["data"]}
        assert execute(client, "portfolioHealth", {"maintenance": "missing", "month": "2026-07"})["summary"]["maintenance_pending_projects"] == \
            sum(not project(client, code)["maintenanceComplete"] for code in ("TPL149", "WDR101", "ZMJ113"))
        overview = client.get("/api/v1/projects/WDR101").json()["data"]
        assert overview["maintenance_complete"] is True and len(overview["maintenance_checklist"]) == 6


def test_maintenance_endpoints_run_ontology_actions(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        created = client.post("/api/v1/projects/WDR101/milestones", json={"name": "需求确认", "planned_end": "2026-09-30"})
        assert created.status_code == 201, created.text
        milestone_id = created.json()["data"]["milestone_id"]
        assert client.patch("/api/v1/projects/WDR101/settings", json={"project_manager": "李经理"}).status_code == 200
        term = client.post("/api/v1/projects/WDR101/payment-terms", json={"name": "首付款", "planned_amount": "1000"})
        assert term.status_code == 201 and term.json()["data"]["payment_term_id"]

        log = client.get("/api/v1/ontology/action-log", params={"objectType": "Project", "primaryKey": "WDR101"}).json()["data"]
        assert {entry["actionType"] for entry in log} >= {"createMilestone", "configureProject", "createPaymentTerm"}
        assert {entry["channel"] for entry in log} == {"ontology"}, "no second, legacy write path"

        # The REST path names the project; an object of another project is not found there.
        assert client.patch(f"/api/v1/projects/TPL149/milestones/{milestone_id}", json={"name": "x"}).status_code == 404
        assert client.delete(f"/api/v1/projects/TPL149/milestones/{milestone_id}").status_code == 404
        assert client.patch("/api/v1/projects/NOPE/settings", json={"notes": "x"}).status_code == 404
        rejected = client.patch("/api/v1/projects/WDR101/settings", json={"contract_type": "construction", "planned_person_days": None})
        assert rejected.status_code == 422 and "预算人天" in rejected.json()["detail"], "the action's criterion decides"


def test_new_milestone_dates_are_checked_against_themselves(tmp_path):
    """A new milestone's dates were compared with the project's planned start (the action's target)."""
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.patch("/api/v1/projects/WDR101/settings", json={"planned_start": "2026-10-01", "planned_end": "2026-12-31"}).status_code == 200
        early = client.post("/api/v1/ontology/actions/createMilestone/apply", json={"parameters": {
            "project": "WDR101", "name": "前期调研", "plannedEnd": "2026-09-15"}})
        assert early.status_code == 200, early.text
        wrong = client.post("/api/v1/ontology/actions/createMilestone/apply", json={"parameters": {
            "project": "WDR101", "name": "倒序", "plannedStart": "2026-09-20", "plannedEnd": "2026-09-15"}})
        assert wrong.status_code == 422 and "计划完成日期不能早于开始日期" in wrong.text


def test_payment_terms_must_be_linked_to_a_milestone(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        term = client.post("/api/v1/projects/WDR101/payment-terms", json={"name": "验收款", "planned_amount": "5000"}).json()["data"]
        obj = client.get(f"/api/v1/ontology/objects/PaymentTerm/{term['payment_term_id']}").json()["data"]["properties"]
        assert obj["maintenanceComplete"] is False and labels(obj["maintenanceChecklist"], False) == ["关联里程碑"]
        assert obj["nodeStatus"] == "unscheduled" and obj["plannedDate"] is None

        worklist = execute(client, "maintenanceWorklist", {"objectType": "PaymentTerm"})
        assert worklist["summary"] == [{"objectType": "PaymentTerm", "requirement": "linkMilestone", "label": "关联里程碑",
                                        "action": "editMilestone", "applicable": 1, "missing": 1, "maintained": 0}]
        assert [row["primaryKey"] for row in worklist["data"]] == [term["payment_term_id"]]

        linked = client.post("/api/v1/ontology/actions/createMilestone/apply", json={"parameters": {
            "project": "WDR101", "name": "验收", "plannedEnd": "2026-12-20", "paymentTermId": term["payment_term_id"]}})
        assert linked.status_code == 200, linked.text
        obj = client.get(f"/api/v1/ontology/objects/PaymentTerm/{term['payment_term_id']}").json()["data"]["properties"]
        assert obj["maintenanceComplete"] is True and obj["plannedDate"] == "2026-12-20" and obj["paymentNodeId"]
        assert execute(client, "maintenanceWorklist", {"objectType": "PaymentTerm"})["data"] == []



def test_pre_sales_and_ended_projects_are_exempt_from_manager_and_dates(tmp_path):
    """PM and planned start/end apply to delivery projects only: not pre-sales, not ended or closed."""
    dated = {"指定项目经理", "填写计划开始日期", "填写计划结束日期"}
    with TestClient(create_app(settings(tmp_path))) as client:
        def pending(code, **parameters):
            response = client.post("/api/v1/ontology/actions/configureProject/apply",
                                   json={"parameters": {"project": code, **parameters}})
            assert response.status_code == 200, response.text
            checklist = project(client, code)["maintenanceChecklist"]
            return {entry["label"] for entry in checklist}, set(labels(checklist, False))

        applicable, _ = pending("WDR101", contractType="open_ended", lifecycleStatus="active", projectManager=None)
        assert dated <= applicable, "an active delivery project needs PM and planned dates"
        assert "WDR101" in {row["primaryKey"] for row in execute(client, "maintenanceWorklist", {"requirement": "assignProjectManager"})["data"]}
        applicable, _ = pending("WDR101", contractType="sales")
        assert not dated & applicable, "pre-sales is exempt"
        for status in ("completed", "closed"):
            applicable, _ = pending("WDR101", contractType="open_ended", lifecycleStatus=status)
            assert not dated & applicable, f"{status} is exempt"
            assert {"选择项目类型", "制定里程碑计划"} <= applicable, "the other requirements still apply"
        worklist = execute(client, "maintenanceWorklist", {"requirement": "assignProjectManager"})
        assert "WDR101" not in {row["primaryKey"] for row in worklist["data"]}


def test_object_actions_offer_the_projects_payment_terms_to_choose(tmp_path):
    """The browser's milestone forms list the project's payment terms instead of asking for a raw ID."""
    def choices(client, object_type, key, action):
        actions = client.get(f"/api/v1/ontology/objects/{object_type}/{key}/actions").json()["data"]
        param = next(p for p in next(a for a in actions if a["apiName"] == action)["parameters"] if p["apiName"] == "paymentTermId")
        return {o["label"] for o in param["options"]}, param["currentValue"]

    with TestClient(create_app(settings(tmp_path))) as client:
        first = client.post("/api/v1/projects/WDR101/payment-terms", json={"name": "首付款", "planned_amount": "3000"}).json()["data"]
        client.post("/api/v1/projects/WDR101/payment-terms", json={"name": "验收款", "planned_amount": "5000"})
        client.post("/api/v1/projects/TPL149/payment-terms", json={"name": "其他项目的款", "planned_amount": "1000"})

        assert choices(client, "Project", "WDR101", "createMilestone") == ({"首付款", "验收款"}, None), "only this project's terms"

        created = client.post("/api/v1/ontology/actions/createMilestone/apply", json={"parameters": {
            "project": "WDR101", "name": "签约", "plannedEnd": "2026-11-20", "paymentTermId": first["payment_term_id"]}})
        assert created.status_code == 200, created.text
        milestone = next(m for m in client.get("/api/v1/projects/WDR101").json()["data"]["milestones"] if m["name"] == "签约")
        labels_, current = choices(client, "Milestone", milestone["milestone_id"], "editMilestone")
        assert labels_ == {"首付款", "验收款"} and current == first["payment_term_id"], "the linked term is preselected"



def test_linking_a_payment_term_fills_amount_and_date(tmp_path):
    """Ontology 5.5.0 / 7.0.0: the linked term gives the payment node its amount; the milestone's planned end its date."""
    def term_param(client, object_type, key, action):
        actions = client.get(f"/api/v1/ontology/objects/{object_type}/{key}/actions").json()["data"]
        return next(p for p in next(a for a in actions if a["apiName"] == action)["parameters"] if p["apiName"] == "paymentTermId")

    with TestClient(create_app(settings(tmp_path))) as client:
        first = client.post("/api/v1/projects/TPL149/payment-terms",
                            json={"name": "首付款", "payment_ratio": "30", "planned_amount": "44970"}).json()["data"]
        client.post("/api/v1/projects/TPL149/payment-terms", json={"name": "验收", "planned_amount": "104930"})
        facts = project(client, "TPL149")
        nodes_before = facts["paymentNodeTotal"]  # the example data has independent payment nodes, not from terms
        assert (facts["paymentTermCount"], facts["unlinkedPaymentTermCount"]) == (2, 2)

        # The choice shows the term's amount and ratio (7.0.0 optionDetails).
        param = term_param(client, "Project", "TPL149", "createMilestone")
        assert param["optionDetails"] == ["plannedAmount", "paymentRatio"]
        assert next(o for o in param["options"] if o["label"] == "首付款")["details"] == [
            {"label": "约定金额", "value": "44970.00", "unit": "yuan"}, {"label": "回款比例", "value": "30.00", "unit": "percent"}]

        created = client.post("/api/v1/ontology/actions/createMilestone/apply", json={"parameters": {
            "project": "TPL149", "name": "项目启动", "plannedStart": "2026-09-27", "plannedEnd": "2026-09-30",
            "paymentTermId": first["payment_term_id"]}})
        assert created.status_code == 200, created.text
        detail = client.get("/api/v1/projects/TPL149").json()["data"]
        [node] = [n for n in detail["payment_nodes"] if n["name"] == "首付款"]
        assert (node["planned_amount"], node["planned_date"]) == ("44970.00", "2026-09-30")
        facts = project(client, "TPL149")
        assert (facts["paymentTermCount"], facts["unlinkedPaymentTermCount"], facts["paymentNodeTotal"]) == (2, 1, nodes_before + 1)

        milestone = next(m["milestone_id"] for m in detail["milestones"] if m["name"] == "项目启动")
        assert term_param(client, "Milestone", milestone, "editMilestone")["currentValue"] == first["payment_term_id"]

        # The browser form leaves the preselected term out; the node date still follows the new planned end.
        moved = client.post("/api/v1/ontology/actions/editMilestone/apply", json={"parameters": {"milestone": milestone, "plannedEnd": "2026-10-15"}})
        assert moved.status_code == 200, moved.text
        [node] = [n for n in client.get("/api/v1/projects/TPL149").json()["data"]["payment_nodes"] if n["name"] == "首付款"]
        assert (node["planned_amount"], node["planned_date"]) == ("44970.00", "2026-10-15")


def test_milestones_create_payment_nodes_only_through_terms(tmp_path):
    """Ontology 7.0.0, build package docs/ontology-builds/2026-10-28-retire-milestone-payment-events.yaml."""
    with TestClient(create_app(settings(tmp_path))) as client:
        schema = client.get("/api/v1/ontology/schema").json()["data"]
        for action in ("createMilestone", "editMilestone"):
            params = {p["apiName"] for p in next(a for a in schema["actionTypes"] if a["apiName"] == action)["parameters"]}
            assert not params & {"isPaymentNode", "paymentDate", "paymentAmount"} and "paymentTermId" in params

        legacy = client.post("/api/v1/ontology/actions/createMilestone/apply", json={"parameters": {
            "project": "WDR101", "name": "旧式回款", "plannedEnd": "2026-12-01", "isPaymentNode": True,
            "paymentDate": "2026-12-20", "paymentAmount": "5000"}})
        assert legacy.status_code == 422 and "isPaymentNode" in legacy.text
        plain = client.post("/api/v1/ontology/actions/createMilestone/apply", json={"parameters": {
            "project": "WDR101", "name": "需求评审", "plannedEnd": "2026-12-01"}})
        assert plain.status_code == 200 and client.get("/api/v1/projects/WDR101").json()["data"]["payment_nodes"] == []

        # An existing legacy payment event stays through edits; linking a term replaces it.
        repository = client.app.state.project_repository
        old = repository.create_milestone("WDR101", {"name": "历史回款", "planned_end": "2026-11-01", "is_payment_node": True,
                                                     "payment_date": "2026-11-05", "payment_amount": "3000"})
        renamed = client.post("/api/v1/ontology/actions/editMilestone/apply", json={"parameters": {"milestone": old["milestone_id"], "name": "历史回款（改名）"}})
        assert renamed.status_code == 200, renamed.text
        [kept] = client.get("/api/v1/projects/WDR101").json()["data"]["payment_nodes"]
        assert (kept["planned_amount"], kept["planned_date"]) == ("3000.00", "2026-11-05")
        term = client.post("/api/v1/projects/WDR101/payment-terms", json={"name": "验收款", "planned_amount": "5000"}).json()["data"]
        linked = client.post("/api/v1/ontology/actions/editMilestone/apply", json={"parameters": {
            "milestone": old["milestone_id"], "paymentTermId": term["payment_term_id"]}})
        assert linked.status_code == 200, linked.text
        [node] = client.get("/api/v1/projects/WDR101").json()["data"]["payment_nodes"]
        assert (node["name"], node["planned_amount"], node["planned_date"]) == ("验收款", "5000.00", "2026-11-01")


@pytest.mark.parametrize(
    ("new", "message"),
    [
        ("optionDetails: [plannedAmount, notAProperty]", "notAProperty 不是 PaymentTerm 的属性"),
    ],
)
def test_invalid_parameter_option_details_are_rejected(tmp_path, new, message):
    metadata = tmp_path / "metadata"
    shutil.copytree(DEFAULT_METADATA_DIR, metadata)
    path = metadata / "action-types/project.yaml"
    old = "optionDetails: [plannedAmount, paymentRatio]"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(OntologyMetadataError, match=message):
        load_ontology(metadata)
    misplaced = text.replace("type: date}\n    - {apiName: owner", "type: date, optionDetails: [name]}\n    - {apiName: owner", 1)
    assert misplaced != text
    path.write_text(misplaced, encoding="utf-8")
    with pytest.raises(OntologyMetadataError, match="只有 object_ref 参数可以声明 optionDetails"):
        load_ontology(metadata)


def test_payment_nodes_come_from_payment_terms_only(tmp_path):
    """Ontology 6.0.0, build package docs/ontology-builds/2026-10-28-retire-create-payment-node.yaml."""
    with TestClient(create_app(settings(tmp_path))) as client:
        # R1: no standalone creation, neither as an ontology action nor through the REST wrapper.
        assert client.post("/api/v1/ontology/actions/createPaymentNode/apply", json={"parameters": {
            "project": "WDR101", "name": "尾款", "plannedDate": "2026-12-01", "plannedAmount": "1000"}}).status_code == 404
        retired = client.post("/api/v1/projects/WDR101/payment-nodes", json={"name": "尾款", "planned_date": "2026-12-01", "planned_amount": "1000"})
        assert retired.status_code == 404
        offered = {a["apiName"] for a in client.get("/api/v1/ontology/objects/Project/WDR101/actions").json()["data"]}
        assert "createPaymentNode" not in offered and {"createPaymentTerm", "createMilestone"} <= offered

        # R2: the seeded TPL149 nodes (not from terms) are still edited, confirmed and protected once paid.
        seeded = {n["name"]: n for n in client.get("/api/v1/projects/TPL149").json()["data"]["payment_nodes"]}
        assert {"首期款", "验收尾款"} <= set(seeded)
        node = seeded["验收尾款"]["payment_node_id"]
        def apply(action, parameters):
            return client.post(f"/api/v1/ontology/actions/{action}/apply", json={"parameters": parameters})
        assert apply("editPaymentNode", {"paymentNode": node, "plannedAmount": "75000"}).status_code == 200
        assert apply("confirmPaymentReceived", {"paymentNode": node, "actualAmount": "75000"}).status_code == 200
        assert apply("deletePaymentNode", {"paymentNode": node}).status_code == 422


def test_editing_a_milestone_leaves_a_received_term_payment_alone(tmp_path):
    """Ontology 7.0.0 R3 exception: once the linked payment is received, milestone edits do not touch it."""
    with TestClient(create_app(settings(tmp_path))) as client:
        term = client.post("/api/v1/projects/WDR101/payment-terms", json={"name": "首付款", "planned_amount": "3000"}).json()["data"]
        created = client.post("/api/v1/ontology/actions/createMilestone/apply", json={"parameters": {
            "project": "WDR101", "name": "启动", "plannedEnd": "2026-09-30", "paymentTermId": term["payment_term_id"]}}).json()["data"]
        milestone = next(e["primaryKey"] for e in created["edits"] if e["objectType"] == "Milestone")
        node = next(e["primaryKey"] for e in created["edits"] if e["objectType"] == "PaymentNode")
        paid = client.post("/api/v1/ontology/actions/confirmPaymentReceived/apply", json={"parameters": {"paymentNode": node, "actualAmount": "3000"}})
        assert paid.status_code == 200, paid.text

        edited = client.post("/api/v1/ontology/actions/editMilestone/apply", json={"parameters": {
            "milestone": milestone, "owner": "刘浪", "plannedEnd": "2026-10-20"}})
        assert edited.status_code == 200, edited.text
        [payment] = client.get("/api/v1/projects/WDR101").json()["data"]["payment_nodes"]
        assert (payment["name"], payment["planned_date"], payment["status"]) == ("首付款", "2026-09-30", "paid")


def test_object_detail_resolves_keys_to_referenced_titles(tmp_path):
    """A milestone shows its payment node by name (「验收款」), not by ID; the key stays available."""
    with TestClient(create_app(settings(tmp_path))) as client:
        term = client.post("/api/v1/projects/WDR101/payment-terms", json={"name": "验收款", "planned_amount": "5000"}).json()["data"]
        created = client.post("/api/v1/ontology/actions/createMilestone/apply", json={"parameters": {
            "project": "WDR101", "name": "验收", "plannedEnd": "2026-12-20", "paymentTermId": term["payment_term_id"]}}).json()["data"]
        milestone = next(e["primaryKey"] for e in created["edits"] if e["objectType"] == "Milestone")
        detail = client.get(f"/api/v1/ontology/objects/Milestone/{milestone}").json()["data"]
        node = detail["properties"]["paymentNodeId"]
        assert detail["references"]["paymentNodeId"] == {"objectType": "PaymentNode", "primaryKey": node, "title": "验收款"}
        assert detail["references"]["projectNumber"]["objectType"] == "Project"
        assert "milestoneId" not in detail["references"], "the object's own key is not a reference"
