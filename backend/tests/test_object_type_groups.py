"""D67 (8.10.0, user 2026-11-16): object type groups for exploring the ontology by business area."""
from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology import load_ontology

from test_api import settings


def test_every_object_type_belongs_to_a_group():
    ontology = load_ontology()
    grouped = {member for group in ontology.object_type_groups for member in group.members}
    assert {t.api_name for t in ontology.object_types} == grouped, "a type outside every group would be missing from the group views"
    assert [g.display_name for g in ontology.object_type_groups] == ["项目与合同", "交付与回款", "工时与资源", "经营口径", "AI 写回"]


def test_the_schema_lists_groups_with_only_the_members_a_user_may_see(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        service = client.app.state.ontology_service
        admin = service.schema({"username": "admin", "is_admin": True, "permissions": []})["objectTypeGroups"]
        assert admin[0] == {"apiName": "projectContract", "displayName": "项目与合同",
                            "description": admin[0]["description"], "members": ["Project", "Contract", "Opportunity", "Customer", "CostObject"]}
        crm = service.schema({"username": "crm", "is_admin": False, "permissions": ["crm"]})
        visible = {t["apiName"] for t in crm["objectTypes"]}
        groups = {g["apiName"]: g["members"] for g in crm["objectTypeGroups"]}
        assert groups["projectContract"] == ["Contract", "Opportunity", "Customer"], "only the CRM types a crm-only user may read"
        assert "aiWriteback" not in groups, "a group with no visible member is left out"
        assert all(set(members) <= visible for members in groups.values())
