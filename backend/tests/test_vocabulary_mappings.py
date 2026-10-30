"""External vocabulary reuse (Schema.org, FOAF, Dublin Core) in .claude/skills/tsg-ontology.

The register (references/vocabulary-mappings.yaml) maps internal elements to pinned external terms; the internal
ontology stays the authority. scripts/check_vocabulary_mappings.py enforces the rules of external-vocabularies.md.
"""
import copy
import importlib.util
from pathlib import Path

import pytest
import yaml

SKILL = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "tsg-ontology"
spec = importlib.util.spec_from_file_location("check_vocabulary_mappings", SKILL / "scripts" / "check_vocabulary_mappings.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
REGISTER = yaml.safe_load((SKILL / "references" / "vocabulary-mappings.yaml").read_text(encoding="utf-8"))


def with_mapping(**overrides):
    register = copy.deepcopy(REGISTER)
    base = {"mappingId": "probe", "internalElement": "Employee", "kind": "type", "externalTerm": "foaf:Person",
            "direction": "internal-to-external", "relation": "broader", "internalVersion": "7.7.0",
            "status": "candidate", "condition": "测试"}
    register["mappings"] = [{**base, **overrides}]
    return register


def test_the_register_passes_and_pins_its_sources():
    assert checker.check(REGISTER) == []
    assert {prefix: v["namespace"] for prefix, v in REGISTER["vocabularies"].items()} == {
        "schema": "https://schema.org/", "foaf": "http://xmlns.com/foaf/0.1/", "dcterms": "http://purl.org/dc/terms/"}
    statuses = {m["status"] for m in REGISTER["mappings"]}
    assert "approved" not in statuses, "nothing is approved without a named reviewer"


@pytest.mark.parametrize(("overrides", "message"), [
    ({"internalElement": "Employee.salary", "kind": "property", "externalTerm": "foaf:name"}, "内部属性 Employee.salary 不存在"),
    ({"externalTerm": "owl:sameAs"}, "不允许登记身份等同"),
    ({"externalTerm": "foaf:name"}, "类别不符"),
    ({"externalTerm": "schema:Thing"}, "不在白名单中"),
    ({"internalElement": "Customer.totalContractValue", "kind": "property", "externalTerm": "schema:name"}, "respectsMarking: FINANCE"),
    ({"internalElement": "Project@team", "kind": "property", "externalTerm": "foaf:member"}, "类别应为 link"),
    ({"status": "approved"}, "必须有 reviewer 和 approvedAt"),
    ({"status": "approved", "reviewer": "PMO", "approvedAt": "2026-10-30", "internalVersion": "7.0.0"}, "需重新审核"),
    ({"status": "rejected"}, "rejectionReason"),
    ({"relation": "same"}, "relation='same' 无效"),
    ({"internalElement": "artifact:OntologyBuild.author", "kind": "property", "externalTerm": "dcterms:title"}, "工件字段"),
])
def test_rule_breaks_are_reported(overrides, message):
    errors = checker.check(with_mapping(**overrides))
    assert any(message in e for e in errors), errors


def test_a_marked_property_maps_once_it_respects_the_marking():
    register = with_mapping(internalElement="Customer.totalContractValue", kind="property", externalTerm="schema:name",
                            respectsMarking="FINANCE")
    assert checker.check(register) == []
