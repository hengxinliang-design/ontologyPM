"""Input guards from the ontology-browser boundary test (ontology 8.1.0, requirements baseline D40,
build package docs/ontology-builds/2026-11-03-input-guards.yaml, test report docs/test-reports/2026-11-01-ontology-browser.md).
"""
from datetime import date

from fastapi.testclient import TestClient

from app.main import create_app
from app.project_repository import ProjectRepository

from test_api import settings


def apply(client, action, parameters, *, validate_only=False):
    return client.post(f"/api/v1/ontology/actions/{action}/apply", json={"parameters": parameters, "validateOnly": validate_only})


def failed(response):
    body = response.json()
    if response.status_code == 200:
        data = body["data"]
        return [e["message"] for e in data["parameterErrors"]] + [c["reason"] for c in data["criteria"] if not c["passed"]]
    return [body.get("detail")]


def test_numbers_out_of_range_are_rejected_not_a_server_error(tmp_path):
    """F1: 1e309 crashed the 2-decimal rounding with a 500, for every money and decimal parameter."""
    with TestClient(create_app(settings(tmp_path))) as client:
        for action, parameters in (("createPaymentTerm", {"project": "WDR101", "name": "x", "plannedAmount": "1e309"}),
                                   ("configureProject", {"project": "WDR101", "hoursPerPersonDay": "1e309"}),
                                   ("createPaymentTerm", {"project": "WDR101", "name": "x", "plannedAmount": "1e15"})):
            response = apply(client, action, parameters, validate_only=True)
            assert response.status_code == 200 and "数值超出范围" in " ".join(failed(response)), (action, response.text)
        ok = apply(client, "createPaymentTerm", {"project": "WDR101", "name": "x", "plannedAmount": "999999999999.99"}, validate_only=True)
        assert ok.json()["data"]["valid"] is True


def test_deeply_nested_where_clauses_are_rejected(tmp_path):
    """F2: 3000 nested ``not`` overflowed the recursion with a 500."""
    def nested(levels):
        where = {"type": "eq", "field": "projectNumber", "value": "TPL149"}
        for _ in range(levels):
            where = {"type": "not", "value": where}
        return {"objectSet": {"type": "filter", "objectSet": {"type": "base", "objectType": "Project"}, "where": where}}

    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.post("/api/v1/ontology/objectSets/loadObjects", json=nested(32)).status_code == 200
        deep = client.post("/api/v1/ontology/objectSets/loadObjects", json=nested(3000))
        assert deep.status_code == 422 and "嵌套超过 32 层" in deep.text


def test_payment_cannot_be_received_in_the_future(tmp_path, monkeypatch):
    """F3: a future receipt counted as that year's operating revenue."""
    monkeypatch.setattr(ProjectRepository, "today", lambda self: date(2026, 11, 3))
    with TestClient(create_app(settings(tmp_path))) as client:
        node = client.get("/api/v1/projects/TPL149").json()["data"]["payment_nodes"][0]["payment_node_id"]
        future = apply(client, "confirmPaymentReceived", {"paymentNode": node, "actualAmount": "1", "actualDate": "2026-11-04"})
        assert future.status_code == 422 and "到账日期不能晚于今天（2026-11-03）" in future.text
        assert apply(client, "confirmPaymentReceived", {"paymentNode": node, "actualAmount": "1", "actualDate": "2026-11-03"}).status_code == 200
        legacy = client.post(f"/api/v1/projects/TPL149/payment-nodes/{node}/confirm", json={"actual_date": "2099-01-01"})
        assert legacy.status_code in (404, 422), "the business endpoint goes through the same rule"


def test_hours_per_person_day_and_budget_must_make_sense(tmp_path):
    """F4, F5: 0 hours per person-day turned every person-day into 0; a negative budget was stored."""
    with TestClient(create_app(settings(tmp_path))) as client:
        for hours in ("0", "-1", "24.5", "25"):
            response = apply(client, "configureProject", {"project": "WDR101", "hoursPerPersonDay": hours})
            assert response.status_code == 422 and "每人天小时数必须大于 0 且不超过 24" in response.text, hours
        for hours in ("0.5", "8", "24"):
            assert apply(client, "configureProject", {"project": "WDR101", "hoursPerPersonDay": hours}, validate_only=True).json()["data"]["valid"], hours
        for budget in ("-10", "0"):
            response = apply(client, "configureProject", {"project": "TPL149", "contractType": "construction", "plannedPersonDays": budget})
            assert response.status_code == 422 and "预算人天必须大于 0" in response.text, budget
        project = client.get("/api/v1/projects/TPL149").json()["data"]
        assert project["planned_person_days"] != "-10.00"
        assert apply(client, "configureProject", {"project": "TPL149", "contractType": "construction", "plannedPersonDays": "30"}).status_code == 200


def test_text_made_of_spaces_counts_as_empty(tmp_path):
    """F6: the dry run passed and a milestone or intervention was created with an empty name; the payment-term
    handler refused it at execution in English."""
    with TestClient(create_app(settings(tmp_path))) as client:
        cases = [("createMilestone", {"project": "WDR101", "name": "   ", "plannedEnd": "2026-12-01"}, "名称为必填项"),
                 ("openIntervention", {"project": "WDR101", "title": "  ", "dueDate": "2026-12-01"}, "事项为必填项"),
                 ("createPaymentTerm", {"project": "WDR101", "name": " \t ", "plannedAmount": "1"}, "名称为必填项")]
        for action, parameters, message in cases:
            preview = apply(client, action, parameters, validate_only=True)
            assert preview.json()["data"]["valid"] is False and message in " ".join(failed(preview)), action
            assert apply(client, action, parameters).status_code == 422, action
        project = client.get("/api/v1/projects/WDR101").json()["data"]
        assert not project["milestones"] and not project["interventions"] and not project["payment_terms"]
        trimmed = apply(client, "createMilestone", {"project": "WDR101", "name": "  上线  ", "plannedEnd": "2026-12-01"})
        assert trimmed.status_code == 200
        assert [m["name"] for m in client.get("/api/v1/projects/WDR101").json()["data"]["milestones"]] == ["上线"]
