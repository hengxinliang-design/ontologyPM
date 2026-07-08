import json
import base64
import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def settings(tmp_path: Path) -> Settings:
    base = Path(__file__).resolve().parents[1]
    return Settings(
        base_dir=base,
        database_path=tmp_path / "test.sqlite3",
        source_kind="file",
        source_file=base / "data" / "source_snapshot.example.json",
        source_url=None,
        source_token=None,
        api_token=None,
        record_id_secret="test-secret",
        cors_origins=("http://localhost:5173",),
        auto_import_on_empty=True,
        scheduler_enabled=False,
        sync_hour=3,
        timezone="America/Detroit",
    )


def test_records_and_project_endpoints(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.get("/api/v1/records")
        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["total_records"] == 3
        project = client.get("/api/v1/projects/TPL149").json()["data"]
        contract = project["crm_records"][0]
        assert contract["probability"] == 100
        assert contract["status"] == "成功"
        assert contract["merged_from_opportunity"] is True
        assert project["planned_person_days"] == "30.00"
        assert project["actual_person_days"] == "2.50"


def test_project_portfolio(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.get("/api/v1/projects")
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["total_projects"] == 3
        tpl = next(item for item in body["data"] if item["project_number"] == "TPL149")
        assert tpl["master_category"] == "项目"
        assert tpl["contract_type"] == "construction"
        assert tpl["status"] == "active"
        assert tpl["current_milestone"]["name"] == "接口方案确认"
        assert tpl["payment_summary"]["total_nodes"] == 2
        assert tpl["payment_summary"]["next_node"]["name"] == "首期款"
        assert tpl["budget_hours"] == "240.00"
        assert tpl["actual_hours"] == "20.00"
        assert tpl["budget_utilization"] == 8.3


def test_csv_export(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.get("/api/v1/exports/latest?format=csv")
        assert response.status_code == 200
        assert response.content.startswith(b"\xef\xbb\xbf")
        assert b"TPL149" in response.content


def test_manual_project_setup_milestone_todos_and_confirmation(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        invalid = client.patch(
            "/api/v1/projects/WDR101/settings",
            json={"contract_type": "construction", "planned_person_days": None},
        )
        assert invalid.status_code == 422

        configured = client.patch(
            "/api/v1/projects/WDR101/settings",
            json={
                "contract_type": "construction",
                "lifecycle_status": "active",
                "planned_person_days": "40",
                "project_manager": "Manager Wang",
            },
        )
        assert configured.status_code == 200
        assert configured.json()["data"]["maintenance_reasons"] == ["制定里程碑计划"]

        created = client.post(
            "/api/v1/projects/WDR101/milestones",
            json={
                "name": "需求确认",
                "planned_start": "2026-06-01",
                "planned_end": "2026-06-30",
                "owner": "Manager Wang",
                "deliverable": "需求说明书",
                "is_payment_node": True,
                "payment_date": "2026-07-01",
                "payment_amount": "412500",
            },
        )
        assert created.status_code == 201
        milestone = created.json()["data"]
        assert milestone["payment_amount"] == "412500.00"

        edited = client.patch(
            f"/api/v1/projects/WDR101/milestones/{milestone['milestone_id']}",
            json={
                "name": "需求与范围确认",
                "planned_start": "2026-06-02",
                "planned_end": "2026-07-02",
                "owner": "Admin User",
                "deliverable": "签字版需求说明书",
                "is_payment_node": True,
                "payment_date": "2026-07-05",
                "payment_amount": "420000",
            },
        )
        assert edited.status_code == 200
        milestone = edited.json()["data"]
        assert milestone["name"] == "需求与范围确认"
        assert milestone["payment_amount"] == "420000.00"

        removable = client.post(
            "/api/v1/projects/WDR101/milestones",
            json={
                "name": "可删除待确认节点",
                "planned_start": "2026-08-01",
                "planned_end": "2026-08-15",
                "is_payment_node": True,
                "payment_date": "2026-08-15",
                "payment_amount": "1000",
            },
        ).json()["data"]
        deleted = client.delete(
            f"/api/v1/projects/WDR101/milestones/{removable['milestone_id']}"
        )
        assert deleted.status_code == 200
        after_delete = deleted.json()["data"]
        assert all(
            item["milestone_id"] != removable["milestone_id"]
            for item in after_delete["milestones"]
        )
        assert all(
            item["payment_node_id"] != removable["payment_node_id"]
            for item in after_delete["payment_nodes"]
        )

        standalone_payment = client.post(
            "/api/v1/projects/WDR101/payment-nodes",
            json={
                "name": "独立回款节点",
                "planned_date": "2026-09-01",
                "planned_amount": "10000",
            },
        )
        assert standalone_payment.status_code == 201
        payment = standalone_payment.json()["data"]
        edited_payment = client.patch(
            f"/api/v1/projects/WDR101/payment-nodes/{payment['payment_node_id']}",
            json={
                "name": "独立回款节点-调整",
                "planned_date": "2026-09-10",
                "planned_amount": "12000",
            },
        )
        assert edited_payment.status_code == 200
        assert edited_payment.json()["data"]["planned_amount"] == "12000.00"
        deleted_payment = client.delete(
            f"/api/v1/projects/WDR101/payment-nodes/{payment['payment_node_id']}"
        )
        assert deleted_payment.status_code == 204

        locked_payment = client.post(
            "/api/v1/projects/WDR101/payment-nodes",
            json={
                "name": "已到账回款",
                "planned_date": "2026-09-20",
                "planned_amount": "8000",
            },
        ).json()["data"]
        assert client.post(
            f"/api/v1/projects/WDR101/payment-nodes/{locked_payment['payment_node_id']}/confirm",
            json={"confirmed_by": "Manager Wang"},
        ).status_code == 200
        assert client.delete(
            f"/api/v1/projects/WDR101/payment-nodes/{locked_payment['payment_node_id']}"
        ).status_code == 422
        paid_project = client.get("/api/v1/projects/WDR101").json()["data"]
        assert paid_project["received_amount"] == paid_project["payment_summary"]["actual_amount"]
        assert paid_project["received_amount"] == "8000.00"

        project = client.get("/api/v1/projects/WDR101").json()["data"]
        assert project["maintenance_complete"] is True
        assert project["pending_todos"] == 2
        assert {item["event_type"] for item in project["todo_events"]} == {
            "milestone", "payment"
        }

        assert client.post(
            f"/api/v1/projects/WDR101/milestones/{milestone['milestone_id']}/confirm",
            json={"confirmed_by": "Manager Wang"},
        ).status_code == 200
        locked = client.patch(
            f"/api/v1/projects/WDR101/milestones/{milestone['milestone_id']}",
            json={"name": "不允许修改"},
        )
        assert locked.status_code == 422
        locked_delete = client.delete(
            f"/api/v1/projects/WDR101/milestones/{milestone['milestone_id']}"
        )
        assert locked_delete.status_code == 422
        assert client.post(
            f"/api/v1/projects/WDR101/payment-nodes/{milestone['payment_node_id']}/confirm",
            json={"confirmed_by": "Manager Wang"},
        ).status_code == 200
        completed = client.get("/api/v1/projects/WDR101").json()["data"]
        assert completed["received_amount"] == completed["payment_summary"]["actual_amount"]
        assert completed["received_amount"] == "428000.00"
        assert completed["pending_todos"] == 0


def test_incremental_sync_preserves_manual_fields(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        client.patch(
            "/api/v1/projects/TPL149/settings",
            json={
                "contract_type": "maintenance",
                "planned_person_days": "88",
                "project_manager": "Manual Manager",
            },
        )
        delta = {
            "sync_mode": "incremental",
            "updated_at": "2026-07-03T03:00:00-04:00",
            "records": [
                {
                    "crm_internal_id": "contract-149",
                    "data_type": "contract_order",
                    "project_number": "TPL149",
                    "subject": "TPL149-天津鹏翎集团股份有限公司ERP/MOM与SRM接口项目",
                    "customer": "〖TPL〗",
                    "category": "实施服务",
                    "signed_at": "2026-06-16",
                    "total_amount": "159900.00",
                    "received_amount": "10000.00",
                    "invoiced_amount": "74950.00",
                    "owner": "CRM Owner",
                }
            ],
        }
        snapshot, mode = app.state.sync_service._build_snapshot(delta)
        assert mode == "incremental"
        assert snapshot["validation"]["updated_records"] == 1
        app.state.database.save_snapshot(snapshot)

        project = client.get("/api/v1/projects/TPL149").json()["data"]
        assert project["contract_value"] == "159900.00"
        assert project["project_manager"] == "Manual Manager"
        assert project["contract_type"] == "maintenance"
        assert project["planned_person_days"] == "88.00"
        assert client.get("/api/v1/records").json()["meta"]["total_records"] == 3


def test_work_time_budget_and_customer_confirmation_gap_alerts(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.patch(
            "/api/v1/projects/TPL149/settings",
            json={"contract_type": "construction", "planned_person_days": "3"},
        )
        client.post(
            "/api/v1/time-entries/import",
            json=[{
                "external_id": "GAP-001",
                "project_number": "TPL149",
                "employee_id": "E009",
                "employee_name": "Employee Nine",
                "work_date": "2026-07-02",
                "hours": "8",
                "approved_hours": "4",
                "approval_status": "approved",
                "work_content": "客户待确认工作",
                "source_updated_at": "2026-07-02T18:00:00+08:00",
            }],
        )
        project = client.get("/api/v1/projects/TPL149").json()["data"]
        assert project["reported_person_days"] == "3.50"
        assert project["customer_confirmed_person_days"] == "3.00"
        assert project["confirmation_gap_person_days"] == "0.50"
        codes = {alert["code"] for alert in project["anomaly_alerts"]}
        assert "BUDGET_OVERRUN" in codes
        assert "TIME_CONFIRMATION_GAP" in codes


def test_import_timesheet_fixture_for_2026_january_and_february(tmp_path):
    fixture_path = Path(__file__).parent / "fixtures" / "time_entries_202601_202602.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    entries = fixture["entries"]
    months = {entry["work_date"][:7] for entry in entries}
    assert len(entries) == 1303
    assert months == {"2026-01", "2026-02"}

    with TestClient(create_app(settings(tmp_path))) as client:
        imported = client.post("/api/v1/time-entries/import", json=entries)
        assert imported.status_code == 200
        assert imported.json() == {"imported": 1303}

        imported_again = client.post("/api/v1/time-entries/import", json=entries)
        assert imported_again.status_code == 200
        assert imported_again.json() == {"imported": 1303}

        project = client.get("/api/v1/projects/SEY101").json()["data"]
        assert project["reported_hours"] == "1181.00"
        assert project["customer_confirmed_hours"] == "1141.00"
        assert project["confirmation_gap_hours"] == "40.00"
        assert project["confirmation_gap_person_days"] == "5.00"
        assert project["reported_person_days"] == "147.62"
        assert project["customer_confirmed_person_days"] == "142.62"
        assert project["risk_level"] == "warning"
        assert project["master_category"] == "项目"
        assert project["current_milestone"] is None
        assert project["payment_summary"]["total_nodes"] == 0

        february = next(item for item in project["monthly_time_entries"] if item["month"] == "2026-02")
        january = next(item for item in project["monthly_time_entries"] if item["month"] == "2026-01")
        assert february["reported_hours"] == "424.00"
        assert february["approved_hours"] == "384.00"
        assert february["confirmation_gap_hours"] == "40.00"
        assert february["entry_count"] == 53
        assert january["reported_hours"] == "757.00"
        assert january["approved_hours"] == "757.00"
        assert len(february["entries"]) == 53

        codes = {alert["code"] for alert in project["anomaly_alerts"]}
        assert "TIME_CONFIRMATION_GAP" in codes


def test_import_timesheet_xlsx_upload(tmp_path):
    workbook = _minimal_timesheet_xlsx(
        [
            ["姓名", "部门", "日期", "项目代码", "case", "天数", "工作内容", "流程状态"],
            ["唐旋", "TSG", "2026-02-28", "JJY117", "", "1", "研发领料取值逻辑变更", "流转完成"],
            ["王殿忠", "TSG", "2026-01-13", "MKT800", "", "0.5", "返回保定", "进行中"],
        ]
    )
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/api/v1/time-entries/import-xlsx",
            json={
                "filename": "timesheet.xlsx",
                "content_base64": (
                    "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,"
                    + base64.b64encode(workbook).decode("ascii")
                ),
            },
        )
        assert response.status_code == 200
        assert response.json()["parsed"] == 2
        assert response.json()["imported"] == 2

        imported_again = client.post(
            "/api/v1/time-entries/import-xlsx",
            json={
                "filename": "timesheet.xlsx",
                "content_base64": base64.b64encode(workbook).decode("ascii"),
            },
        )
        assert imported_again.status_code == 200
        assert imported_again.json()["imported"] == 2

        project = client.get("/api/v1/projects/JJY117").json()["data"]
        assert project["reported_hours"] == "8.00"
        assert project["customer_confirmed_hours"] == "8.00"

        pending = client.get("/api/v1/projects/MKT800").json()["data"]
        assert pending["reported_hours"] == "4.00"
        assert pending["customer_confirmed_hours"] == "0.00"
        assert pending["confirmation_gap_hours"] == "4.00"


def test_project_health_filters_by_month_and_project_number(tmp_path):
    fixture_path = Path(__file__).parent / "fixtures" / "time_entries_202601_202602.json"
    entries = json.loads(fixture_path.read_text(encoding="utf-8"))["entries"]

    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.post("/api/v1/time-entries/import", json=entries).status_code == 200

        no_baseline = client.get(
            "/api/v1/project-health?month=2026-02&project_number=SEY101"
        )
        assert no_baseline.status_code == 200
        row = no_baseline.json()["data"][0]
        assert row["project_number"] == "SEY101"
        assert row["period_actual_person_days"] == "53.00"
        assert row["previous_month_person_days"] == "94.62"
        assert row["participant_count"] == 4
        assert row["monthly_trend"] == "下降"
        assert row["health_status"] == "无基线"
        assert row["overrun_warning"] == "待维护预算"

        client.patch(
            "/api/v1/projects/SEY101/settings",
            json={
                "contract_type": "construction",
                "lifecycle_status": "active",
                "planned_person_days": "120",
                "planned_start": "2026-01-01",
                "planned_end": "2026-06-30",
                "project_manager": "PM Test",
            },
        )
        over_budget = client.get(
            "/api/v1/project-health?month=2026-02&project_number=SEY101"
        )
        assert over_budget.status_code == 200
        row = over_budget.json()["data"][0]
        assert row["budget_person_days"] == "120.00"
        assert row["plan_months"] == 6
        assert row["monthly_budget_person_days"] == "20.00"
        assert row["cumulative_actual_person_days"] == "147.62"
        assert row["burn_rate_percent"] == "123.0"
        assert row["remaining_budget_person_days"] == "-27.62"
        assert row["overrun_warning"] == "已超预算"
        assert row["health_status"] == "已超预算"


def test_query_parameters_and_manual_config_validation(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/api/v1/projects?contract_type=construction").status_code == 200
        assert client.get("/api/v1/projects?contract_type=invalid").status_code == 422
        assert client.get("/api/v1/projects?lifecycle_status=active").status_code == 200
        assert client.get("/api/v1/projects?risk_level=warning").status_code == 200
        assert client.get("/api/v1/project-health?month=2026-02").status_code == 200
        assert client.get("/api/v1/project-health?month=2026-2").status_code == 422
        assert client.get("/api/v1/employee-worktime?period=month&month=2026-02").status_code == 200
        assert client.get("/api/v1/employee-worktime?period=week").status_code == 422
        assert client.patch(
            "/api/v1/worktime-baseline",
            json={"monthly_baseline_hours": "-1"},
        ).status_code == 422


def test_employee_worktime_assessment_and_baseline_config(tmp_path):
    fixture_path = Path(__file__).parent / "fixtures" / "time_entries_202601_202602.json"
    entries = json.loads(fixture_path.read_text(encoding="utf-8"))["entries"]

    with TestClient(create_app(settings(tmp_path))) as client:
        baseline = client.get("/api/v1/worktime-baseline")
        assert baseline.status_code == 200
        assert baseline.json()["data"]["monthly_baseline_hours"] == "153.00"

        assert client.post("/api/v1/time-entries/import", json=entries).status_code == 200

        february = client.get(
            "/api/v1/employee-worktime?period=month&month=2026-02&employee_name=查宏斌"
        )
        assert february.status_code == 200
        body = february.json()
        assert body["meta"]["baseline_hours"] == "153.00"
        row = body["data"][0]
        assert row["employee_name"] == "查宏斌"
        assert row["reported_hours"] == "152.00"
        assert row["reported_person_days"] == "19.00"
        assert row["utilization_percent"] == "99.3"
        assert row["status"] == "工时充足"
        assert "project_delivery_ratio_percent" not in row

        yearly = client.get(
            "/api/v1/employee-worktime?period=year&year=2026&employee_name=查宏斌"
        )
        assert yearly.status_code == 200
        assert yearly.json()["data"][0]["reported_hours"] == "400.00"
        assert yearly.json()["data"][0]["status"] == "严重不足"

        updated = client.patch(
            "/api/v1/worktime-baseline",
            json={"monthly_baseline_hours": "200", "notes": "测试调整"},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["monthly_baseline_hours"] == "200.00"

        adjusted = client.get(
            "/api/v1/employee-worktime?period=month&month=2026-02&employee_name=查宏斌"
        ).json()["data"][0]
        assert adjusted["utilization_percent"] == "76.0"
        assert adjusted["status"] == "基本达标"


def _minimal_timesheet_xlsx(rows: list[list[str]]) -> bytes:
    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row):
            ref = f"{_excel_column(column_index)}{row_index}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{_xml_escape(value)}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        "</worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheets><sheet name="sheet1" sheetId="1" r:id="rId1" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
        "</sheets></workbook>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "")
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return output.getvalue()


def _excel_column(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _xml_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
