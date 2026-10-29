"""Signing year (ontology 5.4.0, build package docs/ontology-builds/2026-10-28-signing-year.yaml).

A contract belongs to the year of its CRM 签约时间; a project to the year of its earliest contract. Customers sum the
contracts signed in the server's current year, and customerAnnualSummary sums them for any year.
"""
from dataclasses import replace
from datetime import date

from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology import load_ontology
from app.ontology.derivations.project import _budget_thresholds, derive_project
from app.project_repository import ProjectRepository

from test_api import settings


def objects(client, object_type, **where):
    query = "&".join(f"where.{field}={value}" for field, value in where.items())
    return client.get(f"/api/v1/ontology/objects/{object_type}?pageSize=200&{query}").json()["data"]


def summary(client, **parameters):
    response = client.post("/api/v1/ontology/functions/customerAnnualSummary/execute", json={"parameters": parameters})
    assert response.status_code == 200, response.text
    return response.json()["data"]["result"]


def test_contracts_and_projects_carry_the_signing_year(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        contracts = {o["properties"]["projectNumber"]: o["properties"]["signedYear"] for o in objects(client, "Contract")}
        assert contracts == {"TPL149": 2026, "ZMJ113": 2025}
        projects = {o["primaryKey"]: o["properties"]["signedYear"] for o in objects(client, "Project")}
        assert projects == {"TPL149": 2026, "WDR101": None, "ZMJ113": 2025}, "an opportunity-only project has no year"

        # R4: the object list filters by year, which is what the browser's year filter sends.
        assert [o["properties"]["projectNumber"] for o in objects(client, "Contract", signedYear=2026)] == ["TPL149"]
        assert [o["primaryKey"] for o in objects(client, "Project", signedYear=2026)] == ["TPL149"]
        grouped = client.post("/api/v1/ontology/objectSets/aggregate", json={
            "objectSet": {"type": "base", "objectType": "Contract"},
            "aggregation": [{"type": "sum", "field": "totalAmount", "name": "amount"}],
            "groupBy": [{"type": "exact", "field": "signedYear"}]}).json()["data"]
        assert {row["group"]["signedYear"]: row["metrics"][0]["value"] for row in grouped} == {2025: "20000.00", 2026: "149900.00"}


def test_project_year_is_the_earliest_contract(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        repository = client.app.state.project_repository
        [bundle] = [b for b in repository.project_sources() if b["project_number"] == "ZMJ113"]
        added = {"data_type": "contract_order", "signed_at": "2027-02-01", "total_amount": {"amount": "5000.00"}}
        policy = repository.get_worktime_baseline()
        derived = derive_project(
            "ZMJ113", [*bundle["crm_records"], added], bundle["settings"], [], [], [], [], [], True,
            _budget_thresholds(policy), policy, date(2027, 3, 1),
        )
        assert derived["signed_year"] == 2025, "an added 2027 contract keeps the project in 2025"


def test_customer_annual_summary_groups_contracts_by_signing_year(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        result = summary(client, year=2026)
        assert result["year"] == 2026 and [y["year"] for y in result["years"]] == [2026]
        [tpl] = result["years"][0]["customers"]
        assert (tpl["customer_name"], tpl["contract_count"], tpl["signed_amount"], tpl["invoiced_amount"], tpl["received_amount"]) == (
            "TPL", 1, "149900.00", "74950.00", "0.00")

        everything = summary(client)
        assert [(y["year"], y["signed_amount"]) for y in everything["years"]] == [(2026, "149900.00"), (2025, "20000.00")]
        assert everything["undated_contract_count"] == 0 and everything["redacted_markings"] == []

    secure = replace(settings(tmp_path / "secure"), auth_enabled=True)
    (tmp_path / "secure").mkdir()
    with TestClient(create_app(secure)) as client:
        admin = {"Authorization": "Bearer " + client.post("/api/v1/auth/login", json={"username": "admin", "password": "Tsnt@2026"}).json()["data"]["token"]}
        client.post("/api/v1/users", headers=admin, json={"username": "pmo", "password": "pmo-pass-1", "permissions": ["employeeWorktime"]})
        client.post("/api/v1/users", headers=admin, json={"username": "crm", "password": "crm-pass-1", "permissions": ["crm"]})
        token = client.post("/api/v1/auth/login", json={"username": "crm", "password": "crm-pass-1"}).json()["data"]["token"]
        response = client.post("/api/v1/ontology/functions/customerAnnualSummary/execute", headers={"Authorization": f"Bearer {token}"},
                               json={"parameters": {"year": 2026}})
        assert response.status_code == 200 and response.json()["data"]["result"]["years"][0]["signed_amount"] == "149900.00"
        token = client.post("/api/v1/auth/login", json={"username": "pmo", "password": "pmo-pass-1"}).json()["data"]["token"]
        assert client.post("/api/v1/ontology/functions/customerAnnualSummary/execute", headers={"Authorization": f"Bearer {token}"},
                           json={"parameters": {}}).status_code == 403


def test_customer_current_year_totals_follow_the_server_date(tmp_path, monkeypatch):
    def customers_on(day: date, folder: str):
        monkeypatch.setattr(ProjectRepository, "today", lambda self: day)
        (tmp_path / folder).mkdir()
        with TestClient(create_app(settings(tmp_path / folder))) as client:
            return {o["primaryKey"]: o["properties"] for o in objects(client, "Customer")}

    # 7.5.0 R4: sign-year receipts read as cumulative, not as this year's cash (requirements baseline D5).
    labels = {p.api_name: p.display_name for p in load_ontology().object_type("Customer").properties}
    assert (labels["currentYearReceivedAmount"], labels["currentYearInvoicedAmount"]) == ("本年签订合同的累计回款", "本年签订合同的累计开票")

    in_2026 = customers_on(date(2026, 10, 28), "y2026")
    assert (in_2026["TPL"]["currentYearContractValue"], in_2026["TPL"]["currentYearInvoicedAmount"],
            in_2026["TPL"]["currentYearReceivedAmount"]) == ("149900.00", "74950.00", "0.00")
    assert in_2026["ZMJ"]["currentYearContractValue"] == "0.00", "ZMJ signed in 2025"

    in_2025 = customers_on(date(2025, 12, 31), "y2025")
    assert (in_2025["ZMJ"]["currentYearContractValue"], in_2025["TPL"]["currentYearContractValue"]) == ("20000.00", "0.00")
