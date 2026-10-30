"""Resource metrics in the ontology (ontology 7.8.0, build package docs/ontology-builds/2026-10-30-resource-metrics-in-ontology.yaml).

Utilization, capacity, worktime assessment, staffing recommendation and resource health have one implementation
(app/ontology/derivations/resources.py); the two pages run ontology functions (requirements baseline G4, D13–D15).
"""
from decimal import Decimal

from fastapi.testclient import TestClient

from app import project_repository
from app.main import create_app
from app.ontology import load_ontology
from app.ontology.derivations.resources import allocation, resource_health, worktime_status
from app.ontology.security import Principal, SecurityPolicy

from test_api import settings
from test_pmo_economics import CAPACITY, POLICY, entry, import_entries, month, workdays


def health_labels():
    value_type = next(v for v in load_ontology().value_types if v.api_name == "ResourceHealthStatus")
    return dict(value_type.values)


def utilizations(client, period):
    """Each employee's utilization in ``period`` as every view reports it."""
    ops = client.get("/api/v1/resource-operations", params={"month": period, "page_size": 200}).json()
    worktime = client.get("/api/v1/employee-worktime", params={"period": "month", "month": period}).json()
    health = client.get("/api/v1/department/resource-health", params={"month": period}).json()["data"]
    employees = client.get(f"/api/v1/ontology/objects/Employee?pageSize=200&month={period}").json()["data"]
    return {
        "resourceOperations": {r["employee_id"]: float(r["utilization_percent"]) for r in ops["data"]},
        "employeeWorktime": {r["employee_id"]: float(r["utilization_percent"]) for r in worktime["data"]},
        "resourceHealth": {r["employee_id"]: r["utilization_percent"] for r in health["data"]},
        "Employee": {o["properties"]["employeeId"]: o["properties"]["monthUtilizationPercent"]
                     for o in employees if o["properties"]["monthUtilizationPercent"] is not None},
    }, ops


def test_one_utilization_for_every_view(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        import_entries(client, [entry(f"U{i}", "WDR101", day, hours="9.00", overtime="1.00") for i, day in enumerate(workdays("2026-09", 20))]
                       + [entry(f"V{i}", "TPL149", day, employee="E002", name="Li Si") for i, day in enumerate(workdays("2026-09", 8))])
        views, _ = utilizations(client, "2026-09")
        people = set(views["resourceHealth"])
        assert {"E001", "E002"} <= people
        for name, values in views.items():
            for employee in people:
                assert values[employee] == views["resourceHealth"][employee], (name, employee, values.get(employee))


def test_resource_pages_run_ontology_functions(tmp_path):
    for moved in ("employee_worktime_assessment", "resource_operations", "_employee_worktime_rows", "_capacity_for_period"):
        assert not hasattr(project_repository.ProjectRepository, moved) and not hasattr(project_repository, moved), moved
    ontology = load_ontology()
    policy = SecurityPolicy(ontology)
    project_manager = Principal("pm", False, frozenset({"projects", "resource", "employeeWorktime"}), role="project_manager")
    for name in ("employeeWorktimeAssessment", "resourceOperations"):
        function = ontology.function(name)
        assert function is not None and function.data_scope is None
        assert not policy.can_execute(project_manager, function), "a project manager may not run it"
    with TestClient(create_app(settings(tmp_path))) as client:
        page = client.get("/api/v1/resource-operations").json()
        assert set(page) == {"data", "summary", "baseline", "meta"}
        assert {"available_count", "high_load_count", "overloaded_count", "available_person_days"} <= set(page["summary"])
        via_function = client.post("/api/v1/ontology/functions/resourceOperations/execute", json={"parameters": {}}).json()["data"]["result"]
        assert via_function["summary"] == page["summary"]
        assert client.get("/api/v1/resource-operations", params={"start_month": "2026-09"}).status_code == 422


def test_overload_has_two_named_meanings():
    # Single month over the overload line: 「本月负荷过高」 on the resource page, not 「持续过载」 yet.
    status, available = allocation(Decimal("110"), Decimal("176"), Decimal("160"), Decimal("0"), POLICY)
    assert (status, available) == ("本月负荷过高", Decimal("0"))
    one = resource_health({"2026-08": month("120"), "2026-09": month("176")}, "2026-09", CAPACITY, POLICY)
    assert "overloaded" not in one["statuses"]
    two = resource_health({"2026-08": month("176"), "2026-09": month("176")}, "2026-09", CAPACITY, POLICY)
    assert two["status"] == "overloaded"
    labels = health_labels()
    assert labels["overloaded"] == "持续过载"


def test_first_month_is_marked_not_healthy():
    first = resource_health({"2026-09": month("80")}, "2026-09", CAPACITY, POLICY)
    assert first["status"] == "first_month" and worktime_status(Decimal("50"), POLICY) == "严重不足"
    second = resource_health({"2026-08": month("150"), "2026-09": month("80")}, "2026-09", CAPACITY, POLICY)
    assert second["status"] == "idle"
    tired = resource_health({"2026-09": month("120", overtime="60")}, "2026-09", CAPACITY, POLICY)
    assert tired["status"] == "fatigue", "other findings are still reported in a first month"
    labels = health_labels()
    assert labels["first_month"] == "首月（不评价闲置）"


def test_internal_codes_come_from_investment_settings(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        days = workdays("2026-09", 10)
        import_entries(client, [entry(f"P{i}", "WDR101", day) for i, day in enumerate(days)]
                       + [entry(f"Z{i}", "ZMJ113", day) for i, day in enumerate(days)])
        before, _ = utilizations(client, "2026-09")
        assert "ZMJ113" in {p["project_number"] for p in client.get("/api/v1/projects", params={"page_size": 200}).json()["data"]}

        codes = client.get("/api/v1/investment/settings").json()["data"]["internal_codes"]
        assert client.patch("/api/v1/investment/settings", json={"internal_codes": [*codes, "zmj113"]}).status_code == 200

        after, ops = utilizations(client, "2026-09")
        for view in ("resourceOperations", "employeeWorktime", "resourceHealth", "Employee"):
            assert abs(after[view]["E001"] - before[view]["E001"] / 2) <= 0.06, (view, after[view]["E001"])
        row = next(r for r in ops["data"] if r["employee_id"] == "E001")
        internal = [a for a in row["assignments"] if a.get("is_internal_nonproject")]
        assert internal and internal[0]["reported_person_days"] == "10.00"
        assert "ZMJ113" in ops["meta"]["internal_nonproject_project_code"]
        assert "ZMJ113" not in {p["project_number"] for p in client.get("/api/v1/projects", params={"page_size": 200}).json()["data"]}
