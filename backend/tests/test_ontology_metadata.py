import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology import DEFAULT_METADATA_DIR, OntologyMetadataError, OntologyVersionConflict, load_ontology
from app.ontology.versioning import diff_documents

from test_api import settings


def copy_metadata(tmp_path: Path) -> Path:
    target = tmp_path / "metadata"
    shutil.copytree(DEFAULT_METADATA_DIR, target)
    return target


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def bump(metadata: Path, version: str) -> None:
    manifest = metadata / "ontology.yaml"
    text = manifest.read_text(encoding="utf-8")
    current = next(line for line in text.splitlines() if line.strip().startswith("version: ") and "." in line)
    manifest.write_text(text.replace(current, f"  version: {version}", 1), encoding="utf-8")


def test_metadata_compiles_with_derived_edits_and_stable_checksum():
    ontology = load_ontology()
    assert ontology.version == "7.6.0"
    assert [t.api_name for t in ontology.object_types][:3] == ["Project", "CostObject", "Contract"]
    assert len(ontology.object_types) == 13
    assert len(ontology.link_types) == 18
    assert len(ontology.action_types) == 17
    assert len(ontology.functions) == 16
    project = ontology.object_type("Project")
    assert project.metadata_source == "object-types/project.yaml"
    assert project.property("contractType").edited_by == ("configureProject",)
    assert project.property("contractType").enum_values[0] == ("construction", "系统实施")
    milestone = ontology.object_type("Milestone")
    assert set(milestone.property("status").edited_by) == {"editMilestone", "confirmMilestoneCompletion"}
    edit_action = ontology.action_type("editMilestone")
    status = next(p for p in edit_action.parameters if p.api_name == "status")
    assert [v for v, _ in status.enum_values] == ["planned", "in_progress", "blocked"]
    assert load_ontology().checksum == ontology.checksum


@pytest.mark.parametrize(
    ("file", "old", "new", "message"),
    [
        ("action-types/project.yaml", "handler: configureProject", "handler: missingHandler", "missingHandler"),
        ("action-types/project.yaml", "rule: cappedBudget", "rule: notARule", "notARule"),
        ("action-types/milestone.yaml", "args: {start: plannedStart, end: plannedEnd, label: 计划完成日期}", "args: {begin: plannedStart}", "args 不匹配"),
        ("object-types/project.yaml", "datasource: projects", "datasource: nowhere", "nowhere"),
        ("object-types/project.yaml", "type: enum, valueType: ContractType,", "type: enum,", "必须声明 valueType"),
        ("object-types/project.yaml", "marking: FINANCE, unit: yuan}", "marking: SECRET, unit: yuan}", "SECRET"),
        ("object-types/project.yaml", "  icon: briefcase", "  icon: briefcase\n  colour: blue", "colour"),
        ("object-types/project.yaml", "readPermissions: [projects, health, resource, department]", "readPermissions: [projects, finance]", "finance"),
        ("link-types.yaml", "resolver: {forward: projectTeam, reverse: employeeProjects}", "resolver: {forward: projectTeam, reverse: nope}", "nope"),
        ("action-types/project.yaml", "    - Project.notes\n", "    - Project.notez\n", "Project.notez"),
        ("ontology.yaml", "  version: 7.6.0", "  version: v7.6", "语义化版本号"),
        ("object-types/project.yaml", "backingDatasets: [records,", "backingDatasets: [crm_cache, records,", "crm_cache"),
        ("object-types/project.yaml", ", unit: count}", "}", "数值属性必须声明量纲"),
        ("object-types/project.yaml", ", unit: hour}", ", unit: yuan}", "money 类型与量纲 yuan 必须同时使用"),
        ("object-types/project.yaml", ", unit: personDay}", ", unit: percent}", "percent 类型与量纲 percent 必须同时使用"),
    ],
)
def test_invalid_metadata_is_rejected_with_location(tmp_path, file, old, new, message):
    metadata = copy_metadata(tmp_path)
    edit(metadata / file, old, new)
    with pytest.raises(OntologyMetadataError) as error:
        load_ontology(metadata)
    assert message in str(error.value)


def test_duplicate_definitions_are_rejected(tmp_path):
    metadata = copy_metadata(tmp_path)
    shutil.copy(metadata / "functions.yaml", metadata / "functions-copy.yaml")
    with pytest.raises(OntologyMetadataError) as error:
        load_ontology(metadata)
    assert "重复定义" in str(error.value)


def test_version_registry_governs_metadata_changes(tmp_path):
    metadata = copy_metadata(tmp_path)
    app_settings = replace(settings(tmp_path), ontology_metadata_dir=metadata)

    with TestClient(create_app(app_settings)) as client:
        versions = client.get("/api/v1/ontology/versions").json()["data"]
        assert [(v["version"], v["active"]) for v in versions] == [("7.6.0", True)]
        schema = client.get("/api/v1/ontology/schema").json()["data"]
        assert schema["checksum"] == versions[0]["checksum"]
        assert {v["apiName"] for v in schema["valueTypes"]} >= {"ContractType", "RiskLevel"}

    edit(metadata / "object-types/project.yaml", "  icon: briefcase", "  icon: folder")
    with pytest.raises(OntologyVersionConflict, match="请升级"):
        with TestClient(create_app(app_settings)):
            pass

    bump(metadata, "7.7.0")
    edit(
        metadata / "object-types/project.yaml",
        "    - {apiName: notes, displayName: 备注, type: string, sourceKind: manual, source: project_settings.notes}\n",
        "",
    )
    edit(metadata / "action-types/project.yaml", "    - Project.notes\n", "")
    with TestClient(create_app(app_settings)) as client:
        versions = client.get("/api/v1/ontology/versions").json()["data"]
        assert [(v["version"], v["active"]) for v in versions] == [("7.7.0", True), ("7.6.0", False)]
        diff = client.get("/api/v1/ontology/versions/diff", params={"from": "7.6.0", "to": "7.7.0"}).json()["data"]
        project_change = next(c for c in diff["changes"] if c["kind"] == "ObjectType" and c["apiName"] == "Project")
        paths = {d["path"]: d for d in project_change["details"]}
        assert paths["icon"]["after"] == "folder"
        assert paths["properties.notes"]["change"] == "removed"
        assert project_change["breaking"] is True
        assert diff["summary"]["breaking"] >= 1
        documents = client.get("/api/v1/ontology/versions/7.6.0").json()["data"]
        assert any(doc["kind"] == "ObjectType" and doc["source"] == "object-types/project.yaml" for doc in documents)
        assert client.get("/api/v1/ontology/versions/9.9.9").status_code == 404

    bump(metadata, "7.6.5")
    with pytest.raises(OntologyVersionConflict, match="必须递增"):
        with TestClient(create_app(app_settings)):
            pass


def test_diff_flags_new_required_parameters_as_breaking():
    before = [
        {"kind": "ActionType", "version": "v1", "definition": {
            "apiName": "a", "parameters": [{"apiName": "x", "type": "string", "required": False}]}},
    ]
    after = [
        {"kind": "ActionType", "version": "v1", "definition": {
            "apiName": "a", "parameters": [
                {"apiName": "x", "type": "string", "required": True},
                {"apiName": "y", "type": "string", "required": False},
            ]}},
        {"kind": "Function", "version": "v1", "definition": {"apiName": "f"}},
    ]
    diff = diff_documents(before, after, "1.0.0", "1.1.0")
    assert diff["summary"] == {"added": 1, "removed": 0, "changed": 1, "breaking": 1}
    details = {d["path"]: d for d in diff["changes"][0]["details"]}
    assert details["parameters.x"]["breaking"] is True
    assert details["parameters.y"]["breaking"] is False


def test_version_history_requires_ontology_permission(tmp_path):
    secure = replace(settings(tmp_path), auth_enabled=True)
    with TestClient(create_app(secure)) as client:
        admin = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Tsnt@2026"}
        ).json()["data"]["token"]}
        client.post("/api/v1/users", headers=admin, json={
            "username": "viewer", "password": "viewer-pass-1", "permissions": ["projects"],
        })
        viewer = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "viewer", "password": "viewer-pass-1"}
        ).json()["data"]["token"]}
        assert client.get("/api/v1/ontology/versions", headers=viewer).status_code == 403
        assert client.get("/api/v1/ontology/versions", headers=admin).status_code == 200


def test_numeric_properties_declare_their_dimension(tmp_path):
    """Situation awareness: values carry units, so lint and pages read the dimension instead of guessing from names."""
    ontology = load_ontology()
    numeric = [p for t in ontology.object_types for p in t.properties if p.data_type in {"integer", "decimal", "money", "percent"}]
    assert numeric and all(p.unit for p in numeric)
    project = ontology.object_type("Project")
    assert project.property("contractValue").unit == "yuan"
    assert project.property("reportedPersonDays").unit == "personDay"
    assert project.property("hoursPerPersonDay").unit == "hour", "a name containing PersonDay is still hours"
    assert ontology.object_type("OperatingPolicy").property("planLagTolerancePoints").unit_label == "个百分点"
    with TestClient(create_app(settings(tmp_path))) as client:
        schema = client.get("/api/v1/ontology/schema").json()["data"]
        props = {p["apiName"]: p for p in next(t for t in schema["objectTypes"] if t["apiName"] == "Project")["properties"]}
        assert (props["reportedPersonDays"]["unit"], props["reportedPersonDays"]["unitLabel"]) == ("personDay", "人天")
        assert props["subject"]["unit"] is None

