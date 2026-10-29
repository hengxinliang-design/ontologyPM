"""Opportunity conversion (ontology 5.2.0, build package docs/ontology-builds/2026-10-28-opportunity-conversion.yaml).

An opportunity whose project number already has a contract is converted; its record stays an Opportunity object for
audit and history, marked convertedToContract.
"""
from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology import load_ontology

from test_api import settings


def test_opportunities_keep_converted_records_and_mark_them(tmp_path):
    opportunity = load_ontology().object_type("Opportunity")
    assert "尚未被合同合并" not in opportunity.description
    properties = {p.api_name: p for p in opportunity.properties}
    assert properties["signedAt"].display_name == "预计签单日期"
    assert properties["salesStage"].source.startswith("records.sales_stage")
    listed = [p.api_name for p in opportunity.properties if p.api_name not in (opportunity.primary_key, opportunity.title_property)]
    assert listed[:4] == ["projectNumber", "status", "probability", "convertedToContract"], "the object list shows these columns"

    with TestClient(create_app(settings(tmp_path))) as client:
        objects = client.post("/api/v1/ontology/objectSets/loadObjects", json={"objectSet": {
            "type": "base", "objectType": "Opportunity"}}).json()["data"]
        by_project = {o["properties"]["projectNumber"]: o["properties"] for o in objects}
        assert (by_project["TPL149"]["convertedToContract"], by_project["TPL149"]["status"]) == (True, "成功")
        assert (by_project["WDR101"]["convertedToContract"], by_project["WDR101"]["status"]) == (False, "跟踪")

        open_ones = client.post("/api/v1/ontology/objectSets/loadObjects", json={"objectSet": {
            "type": "filter", "objectSet": {"type": "base", "objectType": "Opportunity"},
            "where": {"type": "eq", "field": "convertedToContract", "value": False}}}).json()["data"]
        assert [o["properties"]["projectNumber"] for o in open_ones] == ["WDR101"]
