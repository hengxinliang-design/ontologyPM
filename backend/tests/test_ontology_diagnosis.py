"""The validate → diagnose → repair loop of .claude/skills/tsg-ontology (scripts/diagnose_ontology.py)."""
import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology import load_ontology
from app.ontology.compiler import DEFAULT_METADATA_DIR

from test_api import settings

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / ".claude" / "skills" / "tsg-ontology" / "scripts"


def load_diagnose():
    spec = importlib.util.spec_from_file_location("diagnose_ontology", SCRIPTS / "diagnose_ontology.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


def codes(findings, severity=None):
    return {f.code for f in findings if severity is None or f.severity == severity}


def test_live_metadata_passes_syntax_consistency_and_pitfall_stages():
    diagnose = load_diagnose()
    findings, compiled = diagnose.check_metadata(DEFAULT_METADATA_DIR)
    assert compiled is not None and findings == []
    assert diagnose.check_pitfalls(DEFAULT_METADATA_DIR) == []


def test_diagnosis_reports_syntax_errors_and_cardinality_mismatches(tmp_path):
    diagnose = load_diagnose()
    broken = tmp_path / "syntax"
    shutil.copytree(DEFAULT_METADATA_DIR, broken)
    project = broken / "object-types" / "project.yaml"
    project.write_text(project.read_text(encoding="utf-8").replace(
        "source: records.project_number,", "source: records.project_number, 也就是项目代码, 主键,", 1), encoding="utf-8")
    findings, compiled = diagnose.check_metadata(broken)
    assert compiled is None and "SYN-SCHEMA" in codes(findings, "critical"), "half-width commas split the flow mapping"

    reversed_link = tmp_path / "cardinality"
    shutil.copytree(DEFAULT_METADATA_DIR, reversed_link)
    links = reversed_link / "link-types.yaml"
    text = links.read_text(encoding="utf-8")
    links.write_text(text.replace("source: Project, target: Contract, cardinality: one_to_many",
                                  "source: Project, target: Contract, cardinality: many_to_one", 1), encoding="utf-8")
    findings, compiled = diagnose.check_metadata(reversed_link)
    assert compiled is not None
    assert [f.location for f in findings if f.code == "CON-CARDINALITY"] == ["Project.contracts → Contract"]


def test_diagnosis_checks_data_against_the_model(tmp_path):
    diagnose = load_diagnose()
    app_settings = settings(tmp_path)
    with TestClient(create_app(app_settings)) as client:
        entry = {"external_id": "D1", "project_number": "NOCRM1", "employee_id": "E1", "employee_name": "张三",
                 "work_date": "2026-03-02", "hours": "8", "approved_hours": "8", "approval_status": "approved",
                 "work_content": "投入", "source_updated_at": "2026-03-02T18:00:00+08:00"}
        assert client.post("/api/v1/time-entries/import", json=[entry]).status_code == 200
    ontology = load_ontology()

    findings = diagnose.check_data(ontology, app_settings.database_path)
    dangling = [f for f in findings if f.code == "DAT-DANGLING" and f.location == "Project.timeEntries → TimeEntry"]
    assert dangling and dangling[0].severity == "minor" and "已声明" in dangling[0].problem
    assert "DAT-POLICY" not in codes(findings)

    # Bypass every validation and break the stored policy: the diagnosis must catch it.
    with sqlite3.connect(app_settings.database_path) as connection:
        connection.execute("UPDATE worktime_baseline_config SET budget_attention_percent = '95'")
    findings = diagnose.check_data(ontology, app_settings.database_path)
    policy = [f for f in findings if f.code == "DAT-POLICY"]
    assert policy and policy[0].severity == "critical" and "预算关注线 95%" in policy[0].problem


def test_build_packages_need_basis_acceptance_and_a_non_empty_exception_table(tmp_path):
    diagnose = load_diagnose()
    ontology = load_ontology()
    package = tmp_path / "build.yaml"
    package.write_text(
        "kind: OntologyBuild\ntopic: 示例\nontologyVersion: '" + ontology.version + "'\n"
        "rules:\n  - id: R1\n    statement: 预算关注线小于高风险线\n    acceptance: 关注线 95% 时返回 422\n"
        "    verifiedBy: [test_that_does_not_exist]\n    ontology: [OperatingPolicy.noSuchProperty]\n"
        "exceptions: []\n", encoding="utf-8")
    findings, summary = diagnose.check_build(package, ontology)
    assert {"BLD-BASIS", "BLD-EXCEPTIONS-EMPTY"} <= codes(findings, "critical"), "no basis and no exceptions both fail the build"
    assert {"BLD-VERIFIED-BY", "BLD-ONTOLOGY-REF"} <= codes(findings, "important")

    # Every delivered package satisfies the rules: release packages completely; design packages may only
    # reference planned elements and tests (minor), never miss a basis, an acceptance or exceptions.
    delivered = sorted((REPO / "docs" / "ontology-builds").glob("*.yaml"))
    assert delivered
    for path in delivered:
        findings, summary = diagnose.check_build(path, ontology)
        lenient = summary["topic"].endswith("（设计稿）") or summary["superseded"]
        blocking = [f"{f.code} {f.location} {f.problem}" for f in findings if not lenient or f.severity != "minor"]
        assert blocking == [], (path.name, blocking)
        assert summary["exceptions"] and not summary["uncovered"], path.name


def test_design_packages_must_target_a_future_version(tmp_path):
    diagnose = load_diagnose()
    ontology = load_ontology()
    package = tmp_path / "design.yaml"
    package.write_text(
        "kind: OntologyBuild\nstage: design\ntopic: 示例\nontologyVersion: '1.0.0'\n"
        "rules:\n  - id: R1\n    statement: 新属性\n    basis: 用户说明\n    acceptance: 返回 1\n"
        "    verifiedBy: [test_planned_later]\n    ontology: [Project.plannedLater]\n"
        "exceptions:\n  - {rule: R1, case: 空值, handling: 不计算, basis: 分母为零}\n", encoding="utf-8")
    findings, _ = diagnose.check_build(package, ontology)
    assert {f.code: f.severity for f in findings} == {
        "BLD-VERSION": "important", "BLD-VERIFIED-BY": "minor", "BLD-ONTOLOGY-REF": "minor"}


def test_lint_requires_evidence_for_derived_judgements(tmp_path):
    """Situation awareness (Kokar et al.): a judgement is an inference about relations and must name its evidence."""
    spec = importlib.util.spec_from_file_location("lint_ontology", SCRIPTS / "lint_ontology.py")
    lint = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = lint
    spec.loader.exec_module(lint)
    assert not [f for f in lint.lint() if f[0] == "依据"], "every judgement in the live metadata names its evidence"

    metadata = tmp_path / "metadata"
    shutil.copytree(DEFAULT_METADATA_DIR, metadata)
    path = metadata / "object-types/project.yaml"
    path.write_text("\n".join(l for l in path.read_text(encoding="utf-8").split("\n") if "apiName: riskReasons," not in l), encoding="utf-8")
    flagged = [f[1] for f in lint.lint(metadata) if f[0] == "依据"]
    assert flagged == ["Project.riskLevel"], "a looked-up status such as PaymentTerm.nodeStatus is not a judgement"
