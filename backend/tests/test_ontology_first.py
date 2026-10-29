"""Ontology-first analytics (ontology 3.1.0, build package docs/ontology-builds/2026-10-26-ontology-first-phase1.yaml).

Base programs keep master data and maintained fields; project analytics are derived in the ontology and the
pages read them through ontology functions.  The former REST endpoints remain as thin wrappers.
"""
import ast
import inspect
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import project_repository as base_module
from app.main import create_app
from app.ontology.compiler import DEFAULT_METADATA_DIR, DerivationError, OntologyMetadataError, load_ontology
from app.ontology.derivations.project import derive_project, project_base
from app.ontology.rules import RULES
from app.project_repository import ProjectRepository

from test_api import settings

ANALYTICS = {
    "list_projects", "project_health", "get_project", "all_projects", "assistant_candidates", "_build_project",
    "_build_all_projects", "_project_health_row", "_overrun_warning", "_health_status", "_todo_event",
    "_current_milestone", "_payment_summary", "_budget_exhaustion_forecast", "compute_project_economics",
}


def execute(client, function, parameters, headers=None):
    response = client.post(f"/api/v1/ontology/functions/{function}/execute", json={"parameters": parameters}, headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()["data"]["result"]


def test_base_programs_hold_no_project_analytics():
    names = {name for name, _ in inspect.getmembers(ProjectRepository)} | set(vars(base_module))
    assert not ANALYTICS & names, sorted(ANALYTICS & names)
    tree = ast.parse(Path(base_module.__file__).read_text(encoding="utf-8"))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any("ontology" in module or "economics" in module for module in imports), imports
    assert {"projectFacts", "projectInvestment"} <= set(RULES.derivations)
    assert "projectEconomics" not in RULES.derivations, "person-day economics is paused (4.0.0)"


def test_project_properties_are_computed_by_declared_derivations(tmp_path):
    project = load_ontology().object_type("Project")
    assert project.derivations == ("maintenanceStatus", "projectFacts", "projectInvestment")
    assert all(p.derived_from in project.derivations for p in project.properties if p.source_kind == "derived")

    # A derived property without a derivation is rejected when the metadata compiles.
    metadata = tmp_path / "metadata"
    shutil.copytree(DEFAULT_METADATA_DIR, metadata)
    path = metadata / "object-types/project.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(", derivedFrom: projectFacts}", "}", 1), encoding="utf-8")
    with pytest.raises(OntologyMetadataError, match="必须是本对象的推导规则之一"):
        load_ontology(metadata)

    # A derivation whose output differs from the metadata fails loudly instead of leaving the property empty.
    path.write_text(text.replace("derivedFrom: projectInvestment}", "derivedFrom: projectFacts}", 1), encoding="utf-8")
    with pytest.raises(DerivationError, match="推导规则 projectFacts 的输出与元数据不一致"):
        with TestClient(create_app(replace(settings(tmp_path), ontology_metadata_dir=metadata))) as client:
            client.get("/api/v1/ontology/objects/Project")


def test_page_functions_match_the_compatibility_endpoints(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        rest = client.get("/api/v1/project-health", params={"month": "2026-07"}).json()
        ontology = execute(client, "portfolioHealth", {"month": "2026-07"})
        assert rest == ontology and rest["data"] and rest["redacted_markings"] == []
        assert client.get("/api/v1/projects/TPL149").json()["data"] == execute(client, "projectOverview", {"project": "TPL149"})
        assert client.get("/api/v1/projects").json() == execute(client, "projectList", {})
        assert client.get("/api/v1/department/review", params={"year": 2026, "month": "2026-07"}).json()["data"] == \
            execute(client, "departmentOperatingReview", {"year": 2026, "month": "2026-07"})
        assert client.get("/api/v1/projects/NOPE").status_code == 404


def test_master_data_matches_derived_facts(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        repository = client.app.state.project_repository
        policy = repository.get_worktime_baseline()
        from app.ontology.derivations.project import _budget_thresholds
        for bundle in repository.project_sources():
            base = project_base(bundle)
            facts = derive_project(
                bundle["project_number"], bundle["crm_records"], bundle["settings"], bundle["time_entries"],
                bundle["milestones"], bundle["payment_nodes"], bundle["payment_terms"], bundle["interventions"],
                True, _budget_thresholds(policy), policy, repository.today(),
            )
            for key in ("subject", "customer", "owner", "category", "contract_value", "invoiced_amount",
                        "crm_received_amount", "contract_type", "lifecycle_status", "planned_person_days"):
                assert base[key] == facts[key], (bundle["project_number"], key)


def test_pages_redact_by_markings(tmp_path):
    with TestClient(create_app(replace(settings(tmp_path), auth_enabled=True))) as client:
        admin = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Tsnt@2026"}).json()["data"]["token"]}

        def user(name: str, permissions: list[str]) -> dict[str, str]:
            client.post("/api/v1/users", headers=admin, json={"username": name, "password": f"{name}-pass-1", "permissions": permissions})
            return {"Authorization": "Bearer " + client.post(
                "/api/v1/auth/login", json={"username": name, "password": f"{name}-pass-1"}).json()["data"]["token"]}

        viewer = user("viewer", ["health"])
        rows = client.get("/api/v1/project-health", params={"month": "2026-07"}, headers=viewer).json()
        assert rows["redacted_markings"] == ["FINANCE", "PERSONNEL"]
        assert rows["summary"]["contract_value"] is None
        assert all(row["contract_value"] is None and row["payment_summary"]["planned_amount"] is None for row in rows["data"])
        assert rows["data"][0]["health_status"], "monitoring stays visible"

        full = client.get("/api/v1/project-health", params={"month": "2026-07"}, headers=admin).json()
        assert full["redacted_markings"] == [] and full["summary"]["contract_value"] is not None

        assert client.post("/api/v1/time-entries/import", headers=admin, json=[{
            "external_id": "OF-1", "project_number": "TPL149", "employee_id": "E9", "employee_name": "Li Si",
            "work_date": "2026-07-01", "hours": "8.00", "approved_hours": "8.00", "approval_status": "approved",
            "work_content": "实施", "source_updated_at": "2026-07-01T00:00:00+08:00"}]).status_code == 200
        manager = user("manager", ["projects"])
        detail = client.get("/api/v1/projects/TPL149", headers=manager).json()["data"]
        assert detail["redacted_markings"] == ["PERSONNEL"] and detail["contract_value"] is not None
        entries = [entry for month in detail["monthly_time_entries"] for entry in month["entries"]]
        assert entries and all(entry.get("hours") is None for entry in entries), "per-person hours need PERSONNEL"
        assert client.get("/api/v1/projects/TPL149", headers=viewer).status_code == 403


def test_derived_projects_are_cached_in_the_object_index(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.get("/api/v1/project-health", params={"month": "2026-07"})
        before = client.get("/api/v1/ontology/index").json()["data"]
        client.get("/api/v1/project-health", params={"month": "2026-07"})
        client.get("/api/v1/projects/TPL149")
        after = client.get("/api/v1/ontology/index").json()["data"]
        project = lambda status: next(t for t in status["objectTypes"] if t["objectType"] == "Project")
        assert project(after)["builds"] == project(before)["builds"], "reads hit the index"
        assert project(after)["hits"] > project(before)["hits"]

        client.patch("/api/v1/projects/TPL149/settings", json={"notes": "改动后重新推导"})
        assert client.get("/api/v1/projects/TPL149").json()["data"]["settings"]["notes"] == "改动后重新推导"
