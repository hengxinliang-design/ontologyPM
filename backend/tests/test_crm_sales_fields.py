import base64
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _settings(tmp_path: Path) -> Settings:
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
    )


def test_sales_opportunity_import_keeps_project_number_stage_and_source_updated_at(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/crm-imports",
            json={
                "data_type": "sales_opportunity",
                "filename": "opportunities.csv",
                "content_base64": base64.b64encode(
                    (
                        "ID,项目编号,客户,机会主题,预计签单日期,可能性,阶段,负责人,状态,更新时间\n"
                        "op-100,PRE100,HST,未以代码开头的机会主题,2026/09/30,60%,项目方案60%,Sales A,跟踪,2026/06/23\n"
                    ).encode("gb18030")
                ).decode(),
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["validation"]["inserted_records"] == 1

        records = client.get(
            "/api/v1/records?data_type=sales_opportunity&sales_stage=%E9%A1%B9%E7%9B%AE%E6%96%B9%E6%A1%8860%25"
        )
        assert records.status_code == 200, records.text
        opportunity = next(
            item
            for item in records.json()["data"]
            if item["project_number"] == "PRE100"
        )
        assert opportunity["subject"] == "未以代码开头的机会主题"
        assert opportunity["signed_at"] == "2026-09-30"
        assert opportunity["sales_stage"] == "项目方案60%"
        assert opportunity["source_updated_at"] == "2026-06-23"
