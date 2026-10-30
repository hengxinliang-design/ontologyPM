"""Resource forecast (ontology 7.9.0, build package docs/ontology-builds/2026-10-30-resource-forecast.yaml).

CQ-W5–W7: capacity per person, demand per project and the gap per month, from tomorrow for three calendar months
(requirements baseline D8, D18–D21).
"""
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology.security import Principal
from app.pm_assistant import AssistantSession

from test_api import settings
from test_pmo_economics import entry, import_entries, workdays

TODAY = date(2026, 10, 30)


def forecast_client(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    client.__enter__()
    client.app.state.project_repository.today = lambda: TODAY
    return client


def configure(client, code, **values):
    response = client.patch(f"/api/v1/projects/{code}/settings", json=values)
    assert response.status_code == 200, response.text


def forecast(client, **parameters):
    response = client.post("/api/v1/ontology/functions/resourceForecast/execute", json={"parameters": parameters})
    assert response.status_code == 200, response.text
    return response.json()["data"]["result"]


def project(result, code):
    return next(p for p in result["projects"] if p["project_number"] == code)


def gap(result, code):
    return next(g for g in result["data_gaps"] if g["project_number"] == code)


def test_forecast_period_capacity_and_people(tmp_path):
    client = forecast_client(tmp_path)
    try:
        import_entries(client, [entry(f"A{i}", "TPL149", day) for i, day in enumerate(workdays("2026-10", 5))]
                       + [entry(f"B{i}", "TPL149", day, employee="E002", name="Li Si") for i, day in enumerate(workdays("2026-10", 3))]
                       + [{**entry("C0", "TPL149", "2026-10-05", employee="E003", name="Draft Only"), "approval_status": "draft"}])
        result = forecast(client)
        assert [m["month"] for m in result["months"]] == ["2026-10", "2026-11", "2026-12"]
        assert result["period_start"] == "2026-10-31" and result["period_end"] == "2026-12-31"
        calendar = client.app.state.project_repository.workday_summary
        assert result["months"][1]["working_days"] == calendar("2026-11-01", "2026-11-30")["working_days"]
        assert result["months"][0]["working_days"] == calendar("2026-10-31", "2026-10-31")["working_days"], "October from tomorrow"

        target = Decimal(result["assumptions"]["minimum_utilization_percent"]) / 100
        people = {p["employee_id"]: p for p in result["people"]}
        assert {"E001", "E002"} <= set(people) and "E003" not in people, "a draft-only person is not on staff"
        assert result["headcount"] == len(people) and result["headcount_month"] == "2026-10"
        for month in result["months"]:
            per_person = Decimal(month["working_days"]) * target
            assert people["E001"]["months"][month["month"]] == f"{per_person:.2f}"
            assert Decimal(month["capacity_person_days"]) == (per_person * len(people)).quantize(Decimal("0.01"))
        assert set(people["E001"]) == {"employee_id", "employee_name", "months", "total"}, "no demand allocated to a person (D21)"
        assert client.post("/api/v1/ontology/functions/resourceForecast/execute", json={"parameters": {"months": 7}}).status_code == 422

        service = client.app.state.ontology_service
        function = service.ontology.function("resourceForecast")
        manager = Principal("pm", False, frozenset({"resource", "projects"}), role="project_manager")
        assert function.data_scope is None and not service.security.can_execute(manager, function)
        admin = AssistantSession(service, {"username": "admin", "is_admin": True, "permissions": []})
        assert "resourceForecast" in {t["name"] for t in admin.tools()}, "the assistant gets it without code changes"
    finally:
        client.__exit__(None, None, None)


def test_forecast_demand_from_budget_and_run_rate(tmp_path):
    client = forecast_client(tmp_path)
    try:
        import_entries(client, [entry(f"Z{m}{i}", "ZMJ113", day) for m in ("2026-08", "2026-09", "2026-10")
                                for i, day in enumerate(workdays(m, 10))])
        configure(client, "TPL149", contract_type="construction", planned_person_days="200", planned_start="2026-09-01",
                  planned_end="2026-12-31", hours_per_person_day="8")
        configure(client, "ZMJ113", contract_type="open_ended", planned_start="2026-08-01", planned_end="2026-12-31")
        reported = Decimal(client.get("/api/v1/ontology/objects/Project/TPL149").json()["data"]["properties"]["reportedPersonDays"])
        result = forecast(client)

        budget = project(result, "TPL149")
        assert budget["method"] == "budget"
        assert abs(sum(Decimal(v) for v in budget["months"].values()) - (Decimal("200") - reported)) <= Decimal("0.03")

        run_rate = project(result, "ZMJ113")
        assert run_rate["method"] == "run_rate" and "10.00" in run_rate["basis"], run_rate["basis"]
        assert run_rate["months"]["2026-11"] == "10.00" and run_rate["months"]["2026-12"] == "10.00"

        # Units: a 6-hour project person-day is 0.75 of a standard 8-hour person-day (principle 5).
        configure(client, "TPL149", hours_per_person_day="6")
        six = project(forecast(client), "TPL149")
        assert abs(sum(Decimal(v) for v in six["months"].values()) - (Decimal("200") - reported) * Decimal("0.75")) <= Decimal("0.03")
    finally:
        client.__exit__(None, None, None)


def test_forecast_gaps_and_data_gaps(tmp_path):
    client = forecast_client(tmp_path)
    try:
        import_entries(client, [entry(f"Z{i}", "ZMJ113", day) for i, day in enumerate(workdays("2026-10", 5))]
                       + [entry(f"T{i}", "TPL149", day) for i, day in enumerate(workdays("2026-10", 3))])
        configure(client, "ZMJ113", contract_type="open_ended")  # recent hours, no plan end
        configure(client, "TPL149", contract_type="construction", planned_person_days="5000",
                  planned_start="2026-09-01", planned_end="2026-12-31")
        result = forecast(client)
        assert gap(result, "ZMJ113")["reason"] == "缺计划结束日期，无法排期"
        assert "ZMJ113" not in {p["project_number"] for p in result["projects"]}
        november = next(m for m in result["months"] if m["month"] == "2026-11")
        assert november["status"] == "shortage" and Decimal(november["gap_person_days"]) > 0
        assert november["top_projects"][0]["project_number"] == "TPL149"

        configure(client, "TPL149", planned_end="2026-10-15")
        overdue = gap(forecast(client), "TPL149")
        assert overdue["reason"].startswith("计划结束 2026-10-15 已过") and Decimal(overdue["remaining_person_days"]) > 0

        configure(client, "TPL149", planned_person_days="1", planned_end="2026-12-31")
        over = forecast(client)
        assert gap(over, "TPL149")["reason"] == "已超预算，未计入后续需求"
        assert "TPL149" not in {p["project_number"] for p in over["projects"]}
    finally:
        client.__exit__(None, None, None)
