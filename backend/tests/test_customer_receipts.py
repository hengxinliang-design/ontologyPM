"""Customer received and invoiced totals (ontology 5.3.0, build package docs/ontology-builds/2026-10-28-customer-receipts.yaml)."""
from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology import load_ontology

from test_api import settings


def test_customers_sum_received_and_invoiced_amounts(tmp_path):
    customer = load_ontology().object_type("Customer")
    properties = {p.api_name: p for p in customer.properties}
    for name in ("totalReceivedAmount", "totalInvoicedAmount"):
        assert (properties[name].marking, properties[name].unit) == ("FINANCE", "yuan")

    with TestClient(create_app(settings(tmp_path))) as client:
        tpl = client.get("/api/v1/ontology/objects/Customer/TPL").json()["data"]["properties"]
        assert (tpl["totalContractValue"], tpl["totalReceivedAmount"], tpl["totalInvoicedAmount"]) == (
            "149900.00", "0.00", "74950.00")
        project = client.get("/api/v1/ontology/objects/Project/TPL149").json()["data"]["properties"]
        assert (project["crmReceivedAmount"], project["invoicedAmount"]) == ("0.00", "74950.00"), "same source as the project"


def test_customer_latest_signing_ignores_opportunity_dates(tmp_path):
    """Ontology 5.4.1, build package docs/ontology-builds/2026-10-28-customer-latest-signing.yaml."""
    with TestClient(create_app(settings(tmp_path))) as client:
        database = client.app.state.database
        with database.connect() as connection:
            connection.execute(
                """INSERT INTO records(snapshot_id, record_id, data_type, project_number, subject, customer, signed_at,
                                       owner, probability, status, updated_at)
                   VALUES (?, 'rec_test_tpl_opportunity', 'sales_opportunity', 'TPL150', 'TPL150-追加接口', 'TPL',
                           '2027-01-01', 'Li Qiang', 30, '跟踪', '2026-10-28T00:00:00+08:00')""",
                (database.active_snapshot_id(),),
            )
            connection.commit()
        customers = {o["primaryKey"]: o["properties"] for o in client.get("/api/v1/ontology/objects/Customer").json()["data"]}
        assert customers["TPL"]["opportunityCount"] == 1, "the opportunity is the customer's"
        assert customers["TPL"]["latestSignedAt"] == "2026-06-16", "its expected signing date is not a signing"
        assert customers["ZMJ"]["latestSignedAt"] == "2025-12-01"
