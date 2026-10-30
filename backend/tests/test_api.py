import json
import base64
import io
import zipfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.pm_assistant import _project_analysis_context, _provider_error


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
        openai_api_key=None,
        pm_assistant_model="gpt-5.2",
        auth_enabled=False,
        require_crm_project_match=False,
        # Unit tests exercise capacity calculations without relying on the
        # external holiday subscription. The service then uses its documented
        # weekday-only fallback deterministically.
        china_holiday_rest_ics_url="http://127.0.0.1:9/rest.ics",
        china_holiday_work_ics_url="http://127.0.0.1:9/work.ics",
    )


def test_local_login_and_menu_permissions(tmp_path):
    secure_settings = replace(settings(tmp_path), auth_enabled=True)
    with TestClient(create_app(secure_settings)) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/api/v1/projects").status_code == 401
        assert client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"}
        ).status_code == 401

        admin_login = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Tsnt@2026"}
        )
        assert admin_login.status_code == 200
        admin_token = admin_login.json()["data"]["token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        assert client.get("/api/v1/auth/me", headers=admin_headers).json()["data"]["is_admin"] is True

        created = client.post(
            "/api/v1/users",
            headers=admin_headers,
            json={
                "username": "viewer",
                "password": "viewer-pass-1",
                "permissions": ["projects"],
            },
        )
        assert created.status_code == 201
        viewer_token = client.post(
            "/api/v1/auth/login", json={"username": "viewer", "password": "viewer-pass-1"}
        ).json()["data"]["token"]
        viewer_headers = {"Authorization": f"Bearer {viewer_token}"}
        assert client.get("/api/v1/projects", headers=viewer_headers).status_code == 200
        assert client.get("/api/v1/records", headers=viewer_headers).status_code == 403
        assert client.get("/api/v1/users", headers=viewer_headers).status_code == 403
        assert client.post("/api/v1/pm-assistant/chat", headers=viewer_headers, json={"message": "项目风险"}).status_code == 503

        restricted = client.post(
            "/api/v1/users",
            headers=admin_headers,
            json={"username": "importer", "password": "importer-pass-1", "permissions": ["sync"]},
        )
        assert restricted.status_code == 201
        importer_token = client.post(
            "/api/v1/auth/login", json={"username": "importer", "password": "importer-pass-1"}
        ).json()["data"]["token"]
        importer_headers = {"Authorization": f"Bearer {importer_token}"}
        assert client.post("/api/v1/pm-assistant/chat", headers=importer_headers, json={"message": "项目风险"}).status_code == 403

        updated = client.patch(
            "/api/v1/users/viewer",
            headers=admin_headers,
            json={"permissions": ["crm"], "is_active": False},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["is_active"] is False
        assert client.get("/api/v1/auth/me", headers=viewer_headers).status_code == 401
        assert client.patch(
            "/api/v1/users/admin", headers=admin_headers, json={"is_active": False}
        ).status_code == 422

        assert client.post("/api/v1/auth/logout", headers=importer_headers).status_code == 200
        assert client.get("/api/v1/auth/me", headers=importer_headers).status_code == 401


def test_pm_assistant_reports_missing_server_configuration(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post("/api/v1/pm-assistant/chat", json={"message": "有哪些风险项目？"})
        project_response = client.post("/api/v1/projects/TPL149/ai-analysis")
    assert response.status_code == 503
    assert "尚未配置模型服务" in response.json()["detail"]
    assert project_response.status_code == 503
    assert "尚未配置模型服务" in project_response.json()["detail"]


def test_pm_assistant_quota_error_is_actionable():
    response = httpx.Response(
        429,
        json={"error": {"code": "insufficient_quota"}},
        request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
    )
    assert "额度不足" in _provider_error(response)


def test_project_analysis_context_aggregates_people_and_does_not_use_cost_rate():
    context = _project_analysis_context(
        {
            "project_number": "TEST001",
            "monthly_time_entries": [
                {
                    "month": "2026-08",
                    "reported_person_days": "2.00",
                    "overtime_person_days": "0.50",
                    "approved_person_days": "2.00",
                    "confirmation_gap_person_days": "0.50",
                    "entry_count": 2,
                    "entries": [
                        {"employee_name": "张三", "hours": "8", "overtime_hours": "4"},
                        {"employee_name": "李四", "hours": "8", "overtime_hours": "0"},
                    ],
                }
            ],
            "milestones": [],
            "payment_nodes": [],
            "crm_records": [],
        }
    )
    assert context["worktime"]["team_top_by_reported_days"][0] == {
        "employee_name": "张三", "reported_person_days": 1.5
    }
    assert "成本单价" in context["data_limitations"]


def test_records_and_project_endpoints(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.get("/api/v1/records")
        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["total_records"] == 4
        converted_opportunity = next(
            row for row in body["data"]
            if row["data_type"] == "sales_opportunity" and row["project_number"] == "TPL149"
        )
        assert converted_opportunity["status"] == "成功"
        project = client.get("/api/v1/projects/TPL149").json()["data"]
        contract = project["crm_records"][0]
        assert contract["probability"] == 100
        assert contract["status"] is None
        assert contract["merged_from_opportunity"] is True
        assert contract["signed_at"] == "2026-06-16"
        assert contract["total_amount"]["amount"] == "149900.00"
        assert contract["received_amount"]["amount"] == "0.00"
        assert contract["invoiced_amount"]["amount"] == "74950.00"
        assert project["planned_person_days"] == "30.00"
        assert project["actual_person_days"] == "0.00"
        assert project["crm_received_amount"] == "0.00"


def test_cursor_pagination_for_crm_and_project_lists(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        first_crm = client.get("/api/v1/records", params={"page_size": 2}).json()
        cursor = first_crm["meta"]["next_cursor"]
        assert cursor
        second_crm = client.get("/api/v1/records", params={"page_size": 2, "cursor": cursor})
        assert second_crm.status_code == 200
        assert not {
            row["record_id"] for row in first_crm["data"]
        }.intersection(row["record_id"] for row in second_crm.json()["data"])
        assert client.get("/api/v1/records", params={"page": 2}).status_code == 422
        assert client.get(
            "/api/v1/records",
            params={"page_size": 2, "cursor": cursor, "data_type": "contract_order"},
        ).status_code == 422

        first_projects = client.get("/api/v1/projects", params={"page_size": 2}).json()
        assert first_projects["meta"]["next_cursor"]
        second_projects = client.get(
            "/api/v1/projects",
            params={"page_size": 2, "cursor": first_projects["meta"]["next_cursor"]},
        ).json()
        assert {
            row["project_number"] for row in first_projects["data"]
        }.isdisjoint(row["project_number"] for row in second_projects["data"])
        assert client.get(
            "/api/v1/projects",
            params={"page_size": 2, "cursor": first_projects["meta"]["next_cursor"], "risk_level": "critical"},
        ).status_code == 422


def test_updated_since_returns_no_continuation_cursor(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        body = client.get(
            "/api/v1/records",
            params={"page_size": 1, "updated_since": "9999-01-01T00:00:00+00:00"},
        ).json()
        assert body["data"] == []
        assert body["meta"]["next_cursor"] is None


def test_request_metrics_include_percentiles_and_import_duration(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/api/v1/projects", params={"page_size": 2}).status_code == 200
        imported = client.post(
            "/api/v1/time-entries/import",
            json=[{
                "external_id": "METRIC-001",
                "project_number": "TPL149",
                "employee_id": "EMP-1",
                "employee_name": "性能测试",
                "work_date": "2026-07-01",
                "hours": "8",
                "approved_hours": "8",
                "approval_status": "approved",
                "work_content": "性能验证",
                "source_updated_at": "2026-07-01T08:00:00+08:00",
            }],
        )
        assert imported.status_code == 200
        summary = client.get("/api/v1/performance").json()["data"]
        projects = next(item for item in summary["endpoints"] if item["endpoint"] == "/api/v1/projects")
        imports = next(item for item in summary["endpoints"] if item["endpoint"] == "/api/v1/time-entries/import")
        assert projects["sample_count"] >= 1
        assert projects["p50_ms"] is not None and projects["p95_ms"] >= projects["p50_ms"]
        assert imports["is_import"] is True
        assert imports["average_returned_rows"] == 1


def test_database_wait_policy_and_date_indexes_are_initialized(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        with client.app.state.database.connect() as connection:
            assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
            indexes = {
                row[1] for row in connection.execute("PRAGMA index_list('time_entries')")
            }
        assert {"idx_time_date_employee_project", "idx_time_date_project"}.issubset(indexes)


def test_pm_assistant_retrieval_uses_bounded_project_candidates(tmp_path, monkeypatch):
    with TestClient(create_app(settings(tmp_path))) as client:
        service = client.app.state.pm_assistant
        original_export = service.projects.records.export_records
        requested_scopes: list[set[str] | None] = []

        def bounded_export(project_numbers=None):
            requested_scopes.append(project_numbers)
            return original_export(project_numbers)

        monkeypatch.setattr(service.projects.records, "export_records", bounded_export)
        service._retrieve("请分析 TPL149 的风险和回款")  # project facts come from the ontology index, built once
        requested_scopes.clear()
        context, sources = service._retrieve("请分析 TPL149 的风险和回款")
        assert context["projects"]
        assert context["projects"][0]["project_number"] == "TPL149"
        assert context["retrieval_scope"]["candidate_projects"] <= 16
        assert context["retrieval_scope"]["candidate_crm_records"] <= 24
        assert all(scope is not None for scope in requested_scopes), "later questions never rebuild every project"
        assert any("项目主数据库" in source for source in sources)


def test_project_portfolio(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.get("/api/v1/projects")
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["total_projects"] == 3
        assert Decimal(body["summary"]["outstanding_receivable_amount"]) == sum(
            (Decimal(row["outstanding_receivable_amount"] or "0") for row in body["data"]),
            Decimal("0"),
        )
        tpl = next(item for item in body["data"] if item["project_number"] == "TPL149")
        assert tpl["master_category"] == "项目"
        assert tpl["contract_type"] == "construction"
        assert tpl["status"] == "active"
        assert "contract_status" not in tpl
        assert tpl["outstanding_receivable_amount"] == "149900.00"
        assert tpl["current_milestone"]["name"] == "接口方案确认"
        assert tpl["payment_summary"]["total_nodes"] == 2
        assert tpl["payment_summary"]["next_node"]["name"] == "首期款"
        assert tpl["budget_hours"] == "240.00"
        assert tpl["actual_hours"] == "0.00"
        assert tpl["budget_utilization"] == 0.0


def test_project_portfolio_fuzzy_search_matches_code_name_and_linked_customer(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/api/v1/crm-imports",
            json={
                "data_type": "contract_order",
                "filename": "fuzzy-search-contract.csv",
                "content_base64": base64.b64encode(
                    (
                        "项目编号,主题,客户,所有者,总金额\n"
                        "FZY901,FZY901-智能制造平台改造,北辰精密制造有限公司,Manager Wang,1000\n"
                    ).encode()
                ).decode(),
            },
        )
        assert response.status_code == 200, response.text

        for query in ("fzy", "制造平台", "北辰精密"):
            body = client.get("/api/v1/projects", params={"q": query}).json()
            assert [row["project_number"] for row in body["data"]] == ["FZY901"]

            health = client.get(
                "/api/v1/project-health", params={"month": "2026-08", "q": query}
            ).json()
            assert [row["project_number"] for row in health["data"]] == ["FZY901"]


def test_contract_csv_accepts_new_headers_and_gb18030_encoding(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        csv_text = (
            "ID,对应客户,主题,产品/服务,我方签约人,签约时间,分类,状态,总金额,回款金额,开票金额\n"
            "1554,ZMJ,ZMJ457-新合同,ISL-DEPM,Li Qiang,2025/4/16,实施服务,结束,\"15,805.06\",\"15,805.06\",\"15,805.06\"\n"
        )
        response = client.post(
            "/api/v1/crm-imports",
            json={
                "data_type": "contract_order",
                "filename": "new-contract-template.csv",
                "content_base64": base64.b64encode(csv_text.encode("gb18030")).decode(),
            },
        )
        assert response.status_code == 200, response.text
        assert {"subject", "owner", "category", "status"}.issubset(
            response.json()["data"]["recognized_fields"]
        )

        record = client.get("/api/v1/projects/ZMJ457/records").json()["data"][0]
        assert record["owner"] == "Li Qiang"
        assert record["signed_at"] == "2025-04-16"
        assert record["category"] == "实施服务"
        assert record["status"] == "结束"
        assert record["total_amount"]["amount"] == "15805.06"
        assert record["received_amount"]["amount"] == "15805.06"
        assert record["invoiced_amount"]["amount"] == "15805.06"


def test_source_status_is_preserved_and_outstanding_receivable_uses_imported_amounts(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/api/v1/crm-imports",
            json={
                "data_type": "contract_order",
                "filename": "completed-contract.csv",
                "content_base64": base64.b64encode(
                    (
                        "crm_internal_id,项目编号,主题,我方签约人,签约时间,分类,状态,总金额,回款金额,开票金额\n"
                        "done-contract,DONE101,DONE101-已结束合同,Manager Wang,2026-08-01,实施服务,结束,100.00,100.00,100.00\n"
                    ).encode()
                ).decode(),
            },
        )
        assert response.status_code == 200, response.text

        completed = client.get("/api/v1/projects/DONE101").json()["data"]
        assert "contract_status" not in completed
        assert completed["lifecycle_status"] == "completed"
        assert completed["status"] == "completed"
        assert completed["sales_owner"] == "Manager Wang"
        assert completed["project_manager"] is None
        assert completed["category"] == "实施服务"
        assert completed["outstanding_receivable_amount"] == "0.00"
        completed_filter = client.get(
            "/api/v1/projects?lifecycle_status=completed&q=DONE101"
        )
        assert completed_filter.status_code == 200
        assert [row["project_number"] for row in completed_filter.json()["data"]] == ["DONE101"]
        assert "completed" in completed_filter.json()["meta"]["lifecycle_statuses"]
        category_filter = client.get("/api/v1/projects?category=实施服务")
        assert category_filter.status_code == 200
        assert "实施服务" in category_filter.json()["meta"]["categories"]
        assert all(
            "实施服务" in row["category"]
            for row in category_filter.json()["data"]
        )
        imported_record = client.get("/api/v1/projects/DONE101/records").json()["data"][0]
        assert imported_record["owner"] == "Manager Wang"
        assert imported_record["category"] == "实施服务"
        assert imported_record["status"] == "结束"

        ongoing = client.get("/api/v1/projects/TPL149").json()["data"]
        assert ongoing["outstanding_receivable_amount"] == "149900.00"

        health_response = client.get(
            "/api/v1/project-health?month=2026-08&project_number=DONE101"
        ).json()
        health = health_response["data"]
        assert "contract_status" not in health[0]
        assert health[0]["project_status"] == "completed"
        assert health[0]["sales_owner"] == "Manager Wang"
        assert "completed" in health_response["meta"]["lifecycle_statuses"]
        completed_health_filter = client.get(
            "/api/v1/project-health?month=2026-08&lifecycle_status=completed&q=DONE101"
        )
        assert [row["project_number"] for row in completed_health_filter.json()["data"]] == ["DONE101"]

        overridden = client.patch(
            "/api/v1/projects/DONE101/settings",
            json={"lifecycle_status": "active"},
        )
        assert overridden.status_code == 200
        assert overridden.json()["data"]["lifecycle_status"] == "active"
        active_filter = client.get(
            "/api/v1/projects?lifecycle_status=active&q=DONE101"
        )
        assert [row["project_number"] for row in active_filter.json()["data"]] == ["DONE101"]
        active_health_filter = client.get(
            "/api/v1/project-health?month=2026-08&lifecycle_status=active&q=DONE101"
        )
        assert [row["project_number"] for row in active_health_filter.json()["data"]] == ["DONE101"]


def test_project_health_warns_about_worktime_after_contract_completion(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/api/v1/crm-imports",
            json={
                "data_type": "contract_order",
                "filename": "completed-contract-with-extra-worktime.csv",
                "content_base64": base64.b64encode(
                    (
                        "crm_internal_id,项目编号,主题,所有者,签约时间,状态,总金额,回款金额,开票金额\n"
                        "extra-contract,EXTRA101,EXTRA101-结束后额外投入,Manager Wang,2026-08-01,结束,100.00,100.00,100.00\n"
                    ).encode()
                ).decode(),
            },
        )
        assert response.status_code == 200, response.text
        response = client.post(
            "/api/v1/time-entries/import",
            json=[
                {
                    "external_id": "EXTRA-WORK-001",
                    "project_number": "EXTRA101",
                    "employee_id": "EXTRA-E001",
                    "employee_name": "额外投入员工",
                    "work_date": "2026-08-15",
                    "hours": "8",
                    "approved_hours": "8",
                    "approval_status": "approved",
                    "work_content": "合同结束后的工作",
                    "source_updated_at": "2026-08-15T18:00:00+08:00",
                }
            ],
        )
        assert response.status_code == 200, response.text

        body = client.get(
            "/api/v1/project-health", params={"month": "2026-08"}
        ).json()
        row = next(item for item in body["data"] if item["project_number"] == "EXTRA101")
        assert row["health_status"] == "额外投入"
        assert row["period_actual_person_days"] == "1.00"
        assert row["risk_reasons"][0] == "来源状态已结束，统计期仍填报 1.00 人天（额外投入）"
        assert body["summary"]["warning_projects"] >= 1


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
                "planned_start": "2026-06-01",
                "planned_end": "2026-07-31",
                "project_manager": "Manager Wang",
            },
        )
        assert configured.status_code == 200
        assert configured.json()["data"]["maintenance_reasons"] == ["制定里程碑计划"]
        assert configured.json()["data"]["planned_start"] == "2026-06-01"
        assert configured.json()["data"]["planned_end"] == "2026-07-31"

        invalid_schedule = client.patch(
            "/api/v1/projects/WDR101/settings",
            json={"planned_start": "2026-08-01", "planned_end": "2026-07-31"},
        )
        assert invalid_schedule.status_code == 422

        # Ontology 7.0.0: a milestone's payment comes from a linked payment term (amount from the term, date = planned end).
        term = client.post("/api/v1/projects/WDR101/payment-terms", json={"name": "需求确认款", "planned_amount": "412500"}).json()["data"]
        created = client.post(
            "/api/v1/projects/WDR101/milestones",
            json={
                "name": "需求确认",
                "planned_start": "2026-06-01",
                "planned_end": "2026-06-30",
                "owner": "Manager Wang",
                "deliverable": "需求说明书",
                "payment_term_id": term["payment_term_id"],
            },
        )
        assert created.status_code == 201
        milestone = created.json()["data"]
        assert (milestone["payment_amount"], milestone["payment_date"]) == ("412500.00", "2026-06-30")

        edited = client.patch(
            f"/api/v1/projects/WDR101/milestones/{milestone['milestone_id']}",
            json={
                "name": "需求与范围确认",
                "planned_start": "2026-06-02",
                "planned_end": "2026-07-02",
                "owner": "Admin User",
                "deliverable": "签字版需求说明书",
                "payment_amount": "420000",
            },
        )
        assert edited.status_code == 200
        milestone = edited.json()["data"]
        assert milestone["name"] == "需求与范围确认"
        assert (milestone["payment_amount"], milestone["payment_date"]) == ("412500.00", "2026-07-02"), "the old field is ignored"

        removable = client.post(
            "/api/v1/projects/WDR101/milestones",
            json={
                "name": "可删除待确认节点",
                "planned_start": "2026-08-01",
                "planned_end": "2026-08-15",
                "payment_term_id": client.post("/api/v1/projects/WDR101/payment-terms",
                                               json={"name": "可删除款", "planned_amount": "1000"}).json()["data"]["payment_term_id"],
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

        # Ontology 6.0.0 retired creating standalone payment nodes; existing ones (seeded here) stay maintainable.
        nodes = client.app.state.project_repository
        payment = nodes.create_payment_node(
            "WDR101", {"name": "独立回款节点", "planned_date": "2026-09-01", "planned_amount": "10000"}
        )
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

        locked_payment = nodes.create_payment_node(
            "WDR101", {"name": "已到账回款", "planned_date": "2026-09-20", "planned_amount": "8000"}
        )
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
        assert completed["received_amount"] == "420500.00", "412500 from the linked term + 8000 confirmed earlier"
        assert completed["pending_todos"] == 0


def test_contract_payment_terms_are_linked_through_milestones(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        created = client.post(
            "/api/v1/projects/WDR101/payment-terms",
            json={
                "name": "蓝图确认款",
                "payment_ratio": "30",
                "planned_amount": "206700",
                "notes": "收到等额发票后 30 日内支付",
            },
        )
        assert created.status_code == 201, created.text
        term = created.json()["data"]
        assert term["payment_ratio"] == "30.00"
        assert term["linked_milestone_id"] is None

        before_link = client.get("/api/v1/projects/WDR101").json()["data"]
        assert len(before_link["payment_terms"]) == 1
        assert before_link["payment_nodes"] == []
        assert before_link["todo_events"] == []
        overview = client.get("/api/v1/projects").json()["data"]
        overview_term = next(row for row in overview if row["project_number"] == "WDR101")["payment_term_summary"]
        assert overview_term == [{
            "name": "蓝图确认款", "payment_ratio": "30.00", "planned_amount": "206700.00",
            "linked_milestone_id": None, "payment_node_id": None,
            "node_status": None, "planned_date": None,
        }]

        milestone_response = client.post(
            "/api/v1/projects/WDR101/milestones",
            json={
                "name": "蓝图设计确认",
                "planned_start": "2026-09-01",
                "planned_end": "2026-09-20",
                "payment_term_id": term["payment_term_id"],
            },
        )
        assert milestone_response.status_code == 201, milestone_response.text
        milestone = milestone_response.json()["data"]
        assert milestone["payment_term_id"] == term["payment_term_id"]
        assert milestone["payment_date"] == "2026-09-20"
        assert milestone["payment_amount"] == "206700.00"

        linked = client.get("/api/v1/projects/WDR101").json()["data"]
        assert linked["payment_terms"][0]["linked_milestone_id"] == milestone["milestone_id"]
        assert linked["payment_nodes"][0]["planned_date"] == "2026-09-20"
        assert linked["payment_nodes"][0]["payment_term_id"] == term["payment_term_id"]
        overview = client.get("/api/v1/projects").json()["data"]
        overview_term = next(row for row in overview if row["project_number"] == "WDR101")["payment_term_summary"][0]
        assert overview_term["node_status"] == "planned"
        assert overview_term["planned_date"] == "2026-09-20"

        updated_term = client.patch(
            f"/api/v1/projects/WDR101/payment-terms/{term['payment_term_id']}",
            json={"name": "蓝图确认款（调整）", "payment_ratio": "30", "planned_amount": "210000"},
        )
        assert updated_term.status_code == 200, updated_term.text
        updated = client.get("/api/v1/projects/WDR101").json()["data"]
        assert updated["payment_nodes"][0]["name"] == "蓝图确认款（调整）"
        assert updated["payment_nodes"][0]["planned_amount"] == "210000.00"

        duplicate = client.post(
            "/api/v1/projects/WDR101/milestones",
            json={
                "name": "不允许重复关联",
                "planned_end": "2026-10-01",
                "payment_term_id": term["payment_term_id"],
            },
        )
        assert duplicate.status_code == 422

        unlinked = client.patch(
            f"/api/v1/projects/WDR101/milestones/{milestone['milestone_id']}",
            json={"payment_term_id": ""},
        )
        assert unlinked.status_code == 200, unlinked.text
        after_unlink = client.get("/api/v1/projects/WDR101").json()["data"]
        assert after_unlink["payment_nodes"] == []
        assert after_unlink["payment_terms"][0]["linked_milestone_id"] is None


def test_project_overview_payment_term_summary_prioritizes_next_linked_milestone(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        first = client.post(
            "/api/v1/projects/WDR101/payment-terms",
            json={"name": "首期款", "payment_ratio": "30", "planned_amount": "3000"},
        )
        latest = client.post(
            "/api/v1/projects/WDR101/payment-terms",
            json={"name": "验收款", "payment_ratio": "70", "planned_amount": "7000"},
        )
        assert first.status_code == 201, first.text
        assert latest.status_code == 201, latest.text

        first_milestone = client.post(
            "/api/v1/projects/WDR101/milestones",
            json={
                "name": "上线",
                "planned_start": "2099-01-01",
                "planned_end": "2099-01-10",
                "payment_term_id": first.json()["data"]["payment_term_id"],
            },
        )
        latest_milestone = client.post(
            "/api/v1/projects/WDR101/milestones",
            json={
                "name": "月结",
                "planned_start": "2099-02-01",
                "planned_end": "2099-02-10",
                "payment_term_id": latest.json()["data"]["payment_term_id"],
            },
        )
        assert first_milestone.status_code == 201, first_milestone.text
        assert latest_milestone.status_code == 201, latest_milestone.text

        # A later edit to a future term must not replace the next linked payment
        # commitment in the overview.
        with client.app.state.database.connect() as connection:
            connection.execute(
                "UPDATE payment_terms SET updated_at = ? WHERE payment_term_id = ?",
                ("2099-01-01T00:00:00+08:00", latest.json()["data"]["payment_term_id"]),
            )
            connection.commit()

        overview = client.get("/api/v1/projects").json()["data"]
        terms = next(row for row in overview if row["project_number"] == "WDR101")["payment_term_summary"]
        assert [term["name"] for term in terms] == ["首期款", "验收款"]
        assert terms[0]["planned_date"] == "2099-01-10"


def test_completed_todos_distinguish_on_time_and_delayed_completion(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        delayed = client.post(
            "/api/v1/projects/WDR101/milestones",
            json={"name": "延期完成里程碑", "planned_end": "2000-01-01"},
        ).json()["data"]
        on_time = client.post(
            "/api/v1/projects/WDR101/milestones",
            json={"name": "按期完成里程碑", "planned_end": "2999-01-01"},
        ).json()["data"]
        assert client.post(
            f"/api/v1/projects/WDR101/milestones/{delayed['milestone_id']}/confirm",
            json={"confirmed_by": "PMO"},
        ).status_code == 200
        assert client.post(
            f"/api/v1/projects/WDR101/milestones/{on_time['milestone_id']}/confirm",
            json={"confirmed_by": "PMO"},
        ).status_code == 200

        completed = [
            item for item in client.get("/api/v1/projects/WDR101").json()["data"]["todo_events"]
            if item["completed"]
        ]
        by_name = {item["name"]: item for item in completed}
        assert by_name["延期完成里程碑"]["completion_state"] == "delayed"
        assert by_name["延期完成里程碑"]["completion_label"].startswith("延期已完成")
        assert by_name["延期完成里程碑"]["delay_days"] > 0
        assert by_name["按期完成里程碑"]["completion_state"] == "on_time"
        assert by_name["按期完成里程碑"]["completion_label"] == "按期完成"
        assert by_name["按期完成里程碑"]["confirmed_by"] == "PMO"


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
        assert client.get("/api/v1/records").json()["meta"]["total_records"] == 4


def test_manual_crm_upload_classifies_new_update_and_unchanged_rows(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        def upload(csv_text: str) -> dict:
            response = client.post(
                "/api/v1/crm-imports",
                json={
                    "data_type": "contract_order",
                    "filename": "contracts.csv",
                    "content_base64": base64.b64encode(csv_text.encode()).decode(),
                },
            )
            assert response.status_code == 200, response.text
            return response.json()["data"]

        unchanged = upload(
            "crm_internal_id,项目编号,主题,所有者,总金额\n"
            "contract-149,TPL149,TPL149-天津鹏翎集团股份有限公司ERP/MOM与SRM接口项目,Li Qiang,\"￥149,900.00\"\n"
        )
        assert unchanged["validation"]["unchanged_records"] == 1
        assert unchanged["validation"]["updated_records"] == 0

        updated = upload(
            "crm_internal_id,项目编号,主题,所有者,总金额\n"
            "contract-149,TPL149,TPL149-天津鹏翎集团股份有限公司ERP/MOM与SRM接口项目,Li Qiang,\"￥150,000.00\"\n"
        )
        assert updated["validation"]["updated_records"] == 1
        assert client.get("/api/v1/projects/TPL149").json()["data"]["contract_value"] == "150000.00"

        inserted = upload(
            "项目编号,主题,所有者,总金额\n"
            "NEW101,NEW101-手工导入合同,Manager Wang,\"￥8,000.00\"\n"
        )
        assert inserted["validation"]["inserted_records"] == 1
        logs_response = client.get("/api/v1/crm-imports").json()
        logs = logs_response["data"]
        assert len(logs) == 3
        assert logs_response["meta"]["total_records"] == 3
        assert logs[0]["data_type"] == "contract_order"
        assert logs[0]["filename"] == "contracts.csv"
        assert logs[0]["parsed_rows"] == 1
        assert logs[0]["inserted_records"] == 1
        assert logs[0]["imported_at"]
        project = client.get("/api/v1/projects/NEW101").json()["data"]
        assert project["subject"] == "NEW101-手工导入合同"


def _legacy_sales_opportunity_import_coverage(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/crm-imports",
            json={
                "data_type": "sales_opportunity",
                "filename": "opportunities.csv",
                "content_base64": base64.b64encode(
                    (
                        "ID,项目编号,客户,机会主题,预计签单日期,可能性,阶段,负责人,状态,更新日期\n"
                        "op-100,PRE100,HST,未以代码开头的机会主题,2026/09/30,60%,项目方案60%,Sales A,跟踪,2026/06/23\n"
                    ).encode("gb18030")
                ).decode(),
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        assert payload["validation"]["inserted_records"] == 1

        records = client.get(
            "/api/v1/records?data_type=sales_opportunity&sales_stage=%E9%A1%B9%E7%9B%AE%E6%96%B9%E6%A1%8860%25"
        )
        assert records.status_code == 200, records.text
        opportunity = next(item for item in records.json()["data"] if item["project_number"] == "PRE100")
        assert opportunity["subject"] == "未以代码开头的机会主题"
        assert opportunity["signed_at"] == "2026-09-30"
        assert opportunity["sales_stage"] == "项目方案60%"
        assert opportunity["source_updated_at"] == "2026-06-23", opportunity


def test_manual_contract_import_keeps_multiple_rows_for_one_project_without_internal_id(tmp_path):
    """CSV templates do not require an internal CRM ID for contract addenda."""
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/api/v1/crm-imports",
            json={
                "data_type": "contract_order",
                "filename": "multiple-contracts.csv",
                "content_base64": base64.b64encode(
                    (
                        "项目编号,主题,我方签约人,签约时间,分类,状态,总金额,回款金额,开票金额\n"
                        "MULTI101,MULTI101-主合同,Sales A,2026-01-01,系统实施,执行中,100.00,10.00,10.00\n"
                        "MULTI101,MULTI101-补充协议,Sales A,2026-02-01,系统实施,执行中,50.00,0.00,0.00\n"
                    ).encode()
                ).decode(),
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["validation"]["inserted_records"] == 2

        records = client.get("/api/v1/projects/MULTI101/records").json()["data"]
        assert [record["subject"] for record in records] == [
            "MULTI101-主合同",
            "MULTI101-补充协议",
        ]
        project = client.get("/api/v1/projects/MULTI101").json()["data"]
        assert project["contract_value"] == "150.00"
        # Imported CRM repayment and manually confirmed payment nodes are
        # intentionally separate sources of truth.
        assert project["crm_received_amount"] == "10.00"
        assert project["received_amount"] == "0.00"


def test_workload_settlement_uses_confirmed_revenue_instead_of_contract_ratio(tmp_path):
    """Open-ended settlement must not clamp CRM receipts to a 100% contract ratio."""
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/api/v1/crm-imports",
            json={
                "data_type": "contract_order",
                "filename": "workload-settlement.csv",
                "content_base64": base64.b64encode(
                    (
                        "项目编号,主题,我方签约人,签约时间,分类,状态,总金额,回款金额,开票金额\n"
                        "OPEN100,OPEN100-年度支持框架合同,Sales A,2026-01-01,实施服务,结束,356569.00,370969.00,370969.00\n"
                    ).encode()
                ).decode(),
            },
        )
        assert response.status_code == 200, response.text

        project = client.get("/api/v1/projects/OPEN100").json()["data"]
        assert project["revenue_settlement_basis"] == "workload"
        assert project["recognized_revenue_amount"] == "370969.00"
        assert project["crm_received_amount"] == "370969.00"
        assert project["outstanding_receivable_amount"] == "0.00"

        portfolio = client.get("/api/v1/projects").json()
        portfolio_project = next(
            item for item in portfolio["data"] if item["project_number"] == "OPEN100"
        )
        assert portfolio_project["revenue_settlement_basis"] == "workload"


def test_milestone_schedule_dates_are_validated(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        invalid_format = client.post(
            "/api/v1/projects/WDR101/milestones",
            json={"name": "日期格式错误", "planned_end": "2026/09/01"},
        )
        assert invalid_format.status_code == 422
        assert "日期格式应为 YYYY-MM-DD" in invalid_format.json()["detail"], "validated by the ontology action"

        invalid_order = client.post(
            "/api/v1/projects/WDR101/milestones",
            json={
                "name": "日期先后错误",
                "planned_start": "2026-09-02",
                "planned_end": "2026-09-01",
            },
        )
        assert invalid_order.status_code == 422
        assert "计划完成日期不能早于开始日期" in invalid_order.json()["detail"], "validated by the ontology action"

        created = client.post(
            "/api/v1/projects/WDR101/milestones",
            json={
                "name": "可维护里程碑",
                "planned_start": "2026-09-01",
                "planned_end": "2026-09-02",
            },
        )
        assert created.status_code == 201, created.text
        milestone_id = created.json()["data"]["milestone_id"]

        invalid_update = client.patch(
            f"/api/v1/projects/WDR101/milestones/{milestone_id}",
            json={"planned_start": "2026-09-03"},
        )
        assert invalid_update.status_code == 422
        assert "计划完成日期不能早于开始日期" in invalid_update.json()["detail"], "validated by the ontology action"


def test_crm_import_logs_paginate_after_ten_rows(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        for index in range(12):
            response = client.post(
                "/api/v1/crm-imports",
                json={
                    "data_type": "contract_order",
                    "filename": f"import-{index:02d}.csv",
                    "content_base64": base64.b64encode(
                        (
                            "crm_internal_id,项目编号,主题,所有者\n"
                            f"contract-{index},LOG{index:03d},LOG{index:03d}-导入日志测试,Li Qiang\n"
                        ).encode()
                    ).decode(),
                },
            )
            assert response.status_code == 200, response.text

        first_page = client.get("/api/v1/crm-imports?page=1&page_size=10").json()
        assert len(first_page["data"]) == 10
        assert first_page["meta"] == {
            "page": 1,
            "page_size": 10,
            "total_records": 12,
            "total_pages": 2,
        }
        assert first_page["data"][0]["filename"] == "import-11.csv"

        second_page = client.get("/api/v1/crm-imports?page=2&page_size=10").json()
        assert len(second_page["data"]) == 2
        assert second_page["meta"]["page"] == 2
        assert second_page["data"][0]["filename"] == "import-01.csv"


def test_manual_crm_upload_rejects_template_without_required_headers(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/api/v1/crm-imports",
            json={
                "data_type": "sales_opportunity",
                "filename": "opportunities.csv",
                "content_base64": base64.b64encode("项目编号,金额\nABC101,100\n".encode()).decode(),
            },
        )
        assert response.status_code == 422
        assert "主题" in response.json()["detail"]


def test_manual_opportunity_upload_normalizes_decimal_probability_and_expected_columns(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        csv_text = (
            "ID,机会主题,客户,负责人,预期金额,预计签单日期,可能性,状态,类型\n"
            "9001,OPP901-导入测试,〖OPP〗,Li Qiang,8800,2026-09-30,0.2,跟踪,ISL\n"
        )
        response = client.post(
            "/api/v1/crm-imports",
            json={
                "data_type": "sales_opportunity",
                "filename": "opportunities.csv",
                "content_base64": base64.b64encode(csv_text.encode()).decode(),
            },
        )
        assert response.status_code == 200, response.text
        record = client.get("/api/v1/records?project_number=OPP901").json()["data"][0]
        assert record["probability"] == 20
        assert record["customer"] == "OPP", "the CRM bracket pair around the customer code is dropped"
        assert record["signed_at"] == "2026-09-30"
        assert record["total_amount"]["amount"] == "8800.00"
        assert record["category"] == "ISL"


def test_manual_crm_upload_preserves_project_maintenance_fields(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.patch(
            "/api/v1/projects/TPL149/settings",
            json={
                "contract_type": "maintenance",
                "planned_person_days": "88",
                "project_manager": "Manual Manager",
            },
        )
        csv_text = (
            "ID,主题,所有者,总金额\n"
            "contract-149,TPL149-天津鹏翎集团股份有限公司ERP/MOM与SRM接口项目,CRM Owner,160000\n"
        )
        response = client.post(
            "/api/v1/crm-imports",
            json={
                "data_type": "contract_order",
                "filename": "contracts.csv",
                "content_base64": base64.b64encode(csv_text.encode()).decode(),
            },
        )
        assert response.status_code == 200, response.text
        project = client.get("/api/v1/projects/TPL149").json()["data"]
        assert project["contract_value"] == "160000.00"
        assert project["contract_type"] == "maintenance"
        assert project["planned_person_days"] == "88.00"
        assert project["project_manager"] == "Manual Manager"


def test_successful_opportunity_is_a_project_without_contract_row(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        csv_text = (
            "ID,机会主题,负责人,可能性,状态,阶段\n"
            "9002,SUC901-已签约但合同待导入,Li Qiang,1,成功,商务谈判仍显示\n"
        )
        response = client.post(
            "/api/v1/crm-imports",
            json={
                "data_type": "sales_opportunity",
                "filename": "successful-opportunity.csv",
                "content_base64": base64.b64encode(csv_text.encode()).decode(),
            },
        )
        assert response.status_code == 200, response.text
        project = client.get("/api/v1/projects/SUC901").json()["data"]
        assert project["contract_count"] == 0
        assert project["opportunity_count"] == 1
        assert project["master_category"] == "项目"


def test_work_time_budget_and_customer_confirmation_gap_alerts(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.patch(
            "/api/v1/projects/TPL149/settings",
            json={"contract_type": "construction", "planned_person_days": "0.5"},
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
        assert project["reported_person_days"] == "1.00"
        assert project["customer_confirmed_person_days"] == "0.50"
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
        assert imported.json() == {"imported": 1303, "updated": 0, "unchanged": 0}

        imported_again = client.post("/api/v1/time-entries/import", json=entries)
        assert imported_again.status_code == 200
        assert imported_again.json() == {"imported": 0, "updated": 0, "unchanged": 1303}

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


def test_unmatched_worktime_code_does_not_create_project_master_record(tmp_path):
    strict_settings = replace(settings(tmp_path), require_crm_project_match=True)
    with TestClient(create_app(strict_settings)) as client:
        imported = client.post(
            "/api/v1/time-entries/import",
            json=[{
                "external_id": "UNMATCHED-WORKTIME-001",
                "project_number": "ORPHAN001",
                "employee_id": "E001",
                "employee_name": "测试人员",
                "work_date": "2026-07-01",
                "hours": "8",
                "approved_hours": "8",
                "approval_status": "approved",
                "work_content": "等待 CRM 主数据关联",
                "source_updated_at": "2026-07-01T08:00:00+08:00",
            }],
        )
        assert imported.status_code == 200
        assert client.get("/api/v1/projects/ORPHAN001").status_code == 404
        project_codes = {row["project_number"] for row in client.get("/api/v1/projects").json()["data"]}
        assert "ORPHAN001" not in project_codes


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
        assert response.json()["unchanged"] == 0
        assert response.json()["date_range"] == {"start": "2026-01-13", "end": "2026-02-28"}

        imported_again = client.post(
            "/api/v1/time-entries/import-xlsx",
            json={
                "filename": "timesheet.xlsx",
                "content_base64": base64.b64encode(workbook).decode("ascii"),
            },
        )
        assert imported_again.status_code == 200
        assert imported_again.json()["imported"] == 0
        assert imported_again.json()["unchanged"] == 2

        # Exports may render the same days differently and may contain a newer
        # workflow status.  Neither presentation change may add another row.
        reformatted = _minimal_timesheet_xlsx(
            [
                ["姓名", "部门", "日期", "项目代码", "case", "天数", "工作内容", "流程状态"],
                ["唐旋", "TSG", "2026-02-28", "JJY117", "", "1.0", "研发领料取值逻辑变更", "进行中"],
            ]
        )
        reformatted_response = client.post(
            "/api/v1/time-entries/import-xlsx",
            json={
                "filename": "timesheet-reformatted.xlsx",
                "content_base64": base64.b64encode(reformatted).decode("ascii"),
            },
        )
        assert reformatted_response.status_code == 200
        assert reformatted_response.json()["imported"] == 0
        assert reformatted_response.json()["unchanged"] == 1

        project = client.get("/api/v1/projects/JJY117").json()["data"]
        assert project["reported_hours"] == "8.00"
        assert project["customer_confirmed_hours"] == "8.00"

        pending = client.get("/api/v1/projects/MKT800").json()["data"]
        assert pending["reported_hours"] == "4.00"
        assert pending["customer_confirmed_hours"] == "0.00"
        assert pending["confirmation_gap_hours"] == "4.00"


def test_time_entry_is_replaced_only_by_a_newer_source_version(tmp_path):
    original = {
        "external_id": "IMMUTABLE-001",
        "project_number": "LOCK901",
        "employee_id": "E901",
        "employee_name": "原始员工",
        "work_date": "2026-07-11",
        "hours": "8",
        "approved_hours": "0",
        "approval_status": "submitted",
        "work_content": "首次填报",
        "source_updated_at": "2026-07-11T18:00:00+08:00",
    }
    same_version = {**original, "hours": "16", "work_content": "尝试覆盖"}
    approved_later = {**original, "approved_hours": "8", "approval_status": "approved",
                      "source_updated_at": "2026-07-12T09:00:00+08:00"}
    with TestClient(create_app(settings(tmp_path))) as client:
        def import_entries(entry):
            return client.post("/api/v1/time-entries/import", json=[entry]).json()

        def stored_entry():
            project = client.get("/api/v1/projects/LOCK901").json()["data"]
            return project["monthly_time_entries"][0]["entries"][0]

        assert import_entries(original) == {"imported": 1, "updated": 0, "unchanged": 0}
        assert import_entries(same_version) == {"imported": 0, "updated": 0, "unchanged": 1}
        assert (stored_entry()["hours"], stored_entry()["work_content"]) == ("8.00", "首次填报")
        # A later approval in the source system now reaches TSG PM.
        assert import_entries(approved_later) == {"imported": 0, "updated": 1, "unchanged": 0}
        assert stored_entry()["approval_status"] == "approved"
        assert import_entries(original) == {"imported": 0, "updated": 0, "unchanged": 1}, "older versions never win"


def test_xlsx_semantic_time_entry_key_blocks_legacy_duplicate_ids(tmp_path):
    """A legacy Excel-generated ID must not bypass the database duplicate guard."""
    original = {
        "external_id": "TIMESHEET-XLSX-LEGACY-001",
        "project_number": "DEDUP901",
        "employee_id": "E901",
        "employee_name": "去重测试员工",
        "work_date": "2026-07-11",
        "hours": "8",
        "overtime_hours": "0",
        "approved_hours": "8",
        "approval_status": "approved",
        "work_content": "同一条工时",
        "source_updated_at": "2026-07-11T18:00:00+08:00",
    }
    same_work_different_id = {**original, "external_id": "TIMESHEET-XLSX-LEGACY-002"}
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.post("/api/v1/time-entries/import", json=[original]).json() == {
            "imported": 1,
            "updated": 0,
            "unchanged": 0,
        }
        assert client.post(
            "/api/v1/time-entries/import", json=[same_work_different_id]
        ).json() == {"imported": 0, "updated": 0, "unchanged": 1}
        project = client.get("/api/v1/projects/DEDUP901").json()["data"]
        assert project["reported_hours"] == "8.00"


def test_import_exported_timesheet_detail_sheet_with_overtime(tmp_path):
    workbook = _multi_sheet_timesheet_xlsx(
        [
            [["项目代码", "汇总人天"], ["OT901", "1.50"]],
            [
                ["姓名", "所属部门", "项目代码", "时间", "天数", "加班天数", "工作内容", "提交时间"],
                ["员工甲", "TSG", "OT901", "2026-07-24", "1", "0.5", "接口联调", "2026-07-25 09:00"],
            ],
        ]
    )
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/api/v1/time-entries/import-xlsx",
            json={"filename": "TSG-工时.xlsx", "content_base64": base64.b64encode(workbook).decode("ascii")},
        )
        assert response.status_code == 200
        assert response.json()["parsed"] == 1
        assert response.json()["date_range"] == {"start": "2026-07-24", "end": "2026-07-24"}

        project = client.get("/api/v1/projects/OT901").json()["data"]
        assert project["reported_hours"] == "12.00"
        assert project["customer_confirmed_hours"] == "12.00"
        assert project["confirmation_gap_hours"] == "0.00"
        july = project["monthly_time_entries"][0]
        assert july["reported_person_days"] == "1.50"
        assert july["overtime_person_days"] == "0.50"
        assert july["entries"][0]["overtime_hours"] == "4.00"

        resource_summary = project["pmo_resource_summary"]
        assert resource_summary == {
            "cost_basis": "人天",
            "monetary_cost_available": False,
            "participant_count": 1,
            "normal_person_days": "1.00",
            "overtime_person_days": "0.50",
            "reported_person_days": "1.50",
            "approved_person_days": "1.50",
            "confirmation_gap_person_days": "0.00",
        }
        resource_row = project["pmo_resource_rows"][0]
        assert resource_row["employee_name"] == "员工甲"
        assert resource_row["normal_person_days"] == "1.00"
        assert resource_row["overtime_person_days"] == "0.50"
        assert resource_row["reported_person_days"] == "1.50"
        assert resource_row["project_effort_share_percent"] == 100.0
        assert resource_row["overtime_ratio_percent"] == 33.3
        assert resource_row["monthly_average_person_days"] == "1.50"

        employee = client.get(
            "/api/v1/employee-worktime?period=month&month=2026-07&employee_name=员工甲"
        ).json()["data"][0]
        assert employee["reported_person_days"] == "1.50"
        assert employee["overtime_person_days"] == "0.50"


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
        assert row["cumulative_participant_count"] == 6
        assert row["monthly_trend"] == "下降"
        assert row["health_status"] == "无基线"
        assert row["overrun_warning"] == "待维护预算"
        assert row["budget_exhaustion_forecast"] == {
            "label": "暂无预算预测", "basis": "请先维护预算人天"
        }

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
        assert row["budget_exhaustion_forecast"] == {
            "label": "预算已超支", "basis": "已超出 27.62 人天"
        }


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
        assert client.patch(
            "/api/v1/worktime-baseline",
            json={"overload_utilization_percent": "90"},
        ).status_code == 422


def test_employee_worktime_assessment_and_baseline_config(tmp_path):
    fixture_path = Path(__file__).parent / "fixtures" / "time_entries_202601_202602.json"
    entries = json.loads(fixture_path.read_text(encoding="utf-8"))["entries"]

    with TestClient(create_app(settings(tmp_path))) as client:
        baseline = client.get("/api/v1/worktime-baseline")
        assert baseline.status_code == 200
        assert baseline.json()["data"]["monthly_baseline_hours"] == "153.00"
        assert baseline.json()["data"]["overload_utilization_percent"] == "100.00"

        assert client.post("/api/v1/time-entries/import", json=entries).status_code == 200

        february = client.get(
            "/api/v1/employee-worktime?period=month&month=2026-02&employee_name=查宏斌"
        )
        assert february.status_code == 200
        body = february.json()
        assert body["meta"]["baseline_hours"] == "160.00"
        assert body["meta"]["working_days"] == 20
        assert body["meta"]["calendar_available"] is False
        assert body["meta"]["latest_work_month"] == "2026-02"
        row = body["data"][0]
        assert row["employee_name"] == "查宏斌"
        assert row["reported_hours"] == "152.00"
        assert row["reported_person_days"] == "19.00"
        assert row["utilization_percent"] == "95.0"
        assert row["status"] == "工时充足"
        assert "project_delivery_ratio_percent" not in row

        high_load = client.get(
            "/api/v1/resource-operations?month=2026-02&employee_name=查宏斌"
        )
        assert high_load.status_code == 200
        assert high_load.json()["data"][0]["allocation_status"] == "负荷较高"

        lowered_overload = client.patch(
            "/api/v1/worktime-baseline",
            json={"overload_utilization_percent": "94"},
        )
        assert lowered_overload.status_code == 200
        assert lowered_overload.json()["data"]["overload_utilization_percent"] == "94.00"
        overloaded = client.get(
            "/api/v1/resource-operations?month=2026-02&employee_name=查宏斌"
        )
        assert overloaded.status_code == 200
        assert overloaded.json()["data"][0]["allocation_status"] == "本月负荷过高"

        yearly = client.get(
            "/api/v1/employee-worktime?period=year&year=2026&employee_name=查宏斌"
        )
        assert yearly.status_code == 200
        assert yearly.json()["data"][0]["reported_hours"] == "400.00"
        assert yearly.json()["data"][0]["status"] == "严重不足"

        updated = client.patch(
            "/api/v1/worktime-baseline",
            json={"standard_hours_per_day": "10", "notes": "测试调整"},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["standard_hours_per_day"] == "10.00"

        adjusted = client.get(
            "/api/v1/employee-worktime?period=month&month=2026-02&employee_name=查宏斌"
        ).json()["data"][0]
        assert adjusted["utilization_percent"] == "76.0"
        assert adjusted["status"] == "基本达标"


def test_resource_operations_identifies_capacity_and_project_staffing_gap(tmp_path):
    entries = [
        {
            "external_id": "RESOURCE-001",
            "project_number": "RESOURCE901",
            "employee_id": "E901",
            "employee_name": "资源员工",
            "work_date": "2026-07-08",
            "hours": "8",
            "overtime_hours": "4",
            "approved_hours": "12",
            "approval_status": "approved",
            "work_content": "计划投入",
            "source_updated_at": "2026-07-08T18:00:00+08:00",
        },
        {
            "external_id": "RESOURCE-INTERNAL-001",
            "project_number": "TSG999",
            "employee_id": "E902",
            "employee_name": "非项目员工",
            "work_date": "2026-07-08",
            "hours": "8",
            "overtime_hours": "4",
            "approved_hours": "12",
            "approval_status": "approved",
            "work_content": "内部非项目工时",
            "source_updated_at": "2026-07-08T18:00:00+08:00",
        },
    ]
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.post("/api/v1/time-entries/import", json=entries).status_code == 200
        settings_response = client.patch(
            "/api/v1/projects/RESOURCE901/settings",
            json={
                "contract_type": "construction",
                "lifecycle_status": "active",
                "planned_person_days": "20",
                "planned_start": "2026-07-01",
                "planned_end": "2026-07-31",
                "project_manager": "PM Test",
            },
        )
        assert settings_response.status_code == 200

        response = client.get("/api/v1/resource-operations?month=2026-07")
        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["month"] == "2026-07"
        assert body["summary"]["total_employees"] == 2
        assert body["summary"]["available_count"] == 2
        assert body["meta"]["excluded_internal_nonproject_entries"] == 1

        employee = next(
            row for row in body["data"] if row["employee_name"] == "资源员工"
        )
        assert employee["allocation_status"] == "可优先调剂"
        assert employee["available_person_days"] == "15.75"
        assert employee["assignments"] == [
            {
                "project_number": "RESOURCE901",
                "project_name": "RESOURCE901",
                "reported_person_days": "1.50",
                "overtime_person_days": "0.50",
            }
        ]
        assert "suggested_projects" not in employee

        unassigned_employee = next(
            row for row in body["data"] if row["employee_name"] == "非项目员工"
        )
        assert unassigned_employee["reported_person_days"] == "0.00"
        assert unassigned_employee["overtime_person_days"] == "0.00"
        assert unassigned_employee["project_count"] == 0
        assert unassigned_employee["internal_nonproject_person_days"] == "1.50"
        # TSG999 is excluded from project load, so this employee keeps the
        # full dynamic July capacity threshold available for reassignment.
        assert unassigned_employee["available_person_days"] == "17.25"
        assert unassigned_employee["assignments"] == [
            {
                "project_number": "TSG999",
                "project_name": "内部非项目工时（可调剂）",
                "reported_person_days": "1.50",
                "overtime_person_days": "0.50",
                "is_internal_nonproject": True,
            }
        ]
        assert unassigned_employee["allocation_status"] == "可优先调剂"

        assert "active_project_checks" not in body
        assert "staffing_candidates" not in body

        projects = client.get("/api/v1/projects").json()["data"]
        assert all(project["project_number"] != "TSG999" for project in projects)


def test_resource_operations_filters_one_employee_over_a_month_range(tmp_path):
    entries = [
        {
            "external_id": "RESOURCE-RANGE-001", "project_number": "RANGE101",
            "employee_id": "E201", "employee_name": "范围员工", "work_date": "2026-06-30",
            "hours": "8", "approved_hours": "8", "approval_status": "approved",
            "work_content": "六月项目投入", "source_updated_at": "2026-06-30T18:00:00+08:00",
        },
        {
            "external_id": "RESOURCE-RANGE-002", "project_number": "RANGE102",
            "employee_id": "E201", "employee_name": "范围员工", "work_date": "2026-07-01",
            "hours": "16", "overtime_hours": "8", "approved_hours": "24", "approval_status": "approved",
            "work_content": "七月项目投入", "source_updated_at": "2026-07-01T18:00:00+08:00",
        },
        {
            "external_id": "RESOURCE-RANGE-003", "project_number": "RANGE103",
            "employee_id": "E202", "employee_name": "其他员工", "work_date": "2026-07-01",
            "hours": "8", "approved_hours": "8", "approval_status": "approved",
            "work_content": "不应匹配", "source_updated_at": "2026-07-01T18:00:00+08:00",
        },
    ]
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.post("/api/v1/time-entries/import", json=entries).status_code == 200
        response = client.get(
            "/api/v1/resource-operations?start_month=2026-06&end_month=2026-07&employee_name=范围"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["start_date"] == "2026-06-01"
        assert body["meta"]["end_date"] == "2026-07-31"
        assert body["meta"]["employee_name"] == "范围"
        assert len(body["data"]) == 1
        assert body["data"][0]["employee_name"] == "范围员工"
        assert body["data"][0]["reported_person_days"] == "4.00"
        assert {item["project_number"] for item in body["data"][0]["assignments"]} == {"RANGE101", "RANGE102"}


def _minimal_timesheet_xlsx(rows: list[list[str]]) -> bytes:
    return _multi_sheet_timesheet_xlsx([rows])


def _multi_sheet_timesheet_xlsx(sheets: list[list[list[str]]]) -> bytes:
    sheet_xmls = [_timesheet_sheet_xml(rows) for rows in sheets]
    sheet_nodes = "".join(
        f'<sheet name="sheet{index}" sheetId="{index}" r:id="rId{index}" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
        for index in range(1, len(sheet_xmls) + 1)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheets>{sheet_nodes}</sheets></workbook>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "")
        archive.writestr("xl/workbook.xml", workbook_xml)
        for index, sheet_xml in enumerate(sheet_xmls, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml)
    return output.getvalue()


def _timesheet_sheet_xml(rows: list[list[str]]) -> str:
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
    return sheet_xml


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
