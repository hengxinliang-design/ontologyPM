from app.db import Database
from app.normalizer import SnapshotValidationError, customer_name, normalize_snapshot


def test_keeps_successful_opportunity_for_crm_audit_and_marks_contract():
    payload = {
        "更新时间": "2026-07-02T09:00:00-04:00",
        "records": [
            {
                "crm_internal_id": "c-1",
                "数据类型": "合同/订单",
                "主题": "TPL149-合同",
                "所有者": "Li Qiang",
                "可能性": "",
                "状态": "",
            },
            {
                "crm_internal_id": "o-1",
                "数据类型": "销售机会",
                "主题": "TPL149-机会",
                "所有者": "Li Qiang",
                "可能性": "100%",
                "状态": "成功",
            },
        ],
    }
    snapshot = normalize_snapshot(payload, record_id_secret="test", timezone="America/Detroit")
    assert len(snapshot["records"]) == 2
    contract = next(row for row in snapshot["records"] if row["data_type"] == "contract_order")
    opportunity = next(row for row in snapshot["records"] if row["data_type"] == "sales_opportunity")
    assert contract["probability"] == 100
    assert contract["status"] is None
    assert contract["merged_from_opportunity"] is True
    assert opportunity["probability"] == 100
    assert opportunity["status"] == "成功"
    assert snapshot["validation"]["merged_opportunities"] == 1


def test_merges_multi_contract_rows_for_same_project_number():
    payload = {
        "records": [
            {"数据类型": "合同/订单", "主题": "ABC101-合同一", "所有者": "A"},
            {"数据类型": "合同/订单", "主题": "ABC101-合同二", "所有者": "A"},
            {
                "数据类型": "销售机会",
                "主题": "ABC101-机会",
                "所有者": "A",
                "可能性": "100%",
                "状态": "成功",
            },
        ]
    }
    snapshot = normalize_snapshot(payload, record_id_secret="test", timezone="America/Detroit")
    assert len(snapshot["records"]) == 3
    contracts = [row for row in snapshot["records"] if row["data_type"] == "contract_order"]
    assert len(contracts) == 2
    assert all(row["probability"] == 100 for row in contracts)
    assert all(row["status"] is None for row in contracts)
    assert snapshot["output_stats"] == {"contract_order": 2, "sales_opportunity": 1}


def test_same_numeric_crm_id_in_contract_and_opportunity_is_type_scoped():
    snapshot = normalize_snapshot(
        {
            "records": [
                {"crm_internal_id": "1783", "数据类型": "合同/订单", "主题": "ABC101-合同", "所有者": "A"},
                {"crm_internal_id": "1783", "数据类型": "销售机会", "主题": "DEF101-商机", "所有者": "B", "可能性": "20%", "状态": "跟踪"},
            ]
        },
        record_id_secret="test",
        timezone="Asia/Shanghai",
    )
    assert len(snapshot["records"]) == 2
    assert len({row["record_id"] for row in snapshot["records"]}) == 2


def test_contract_source_status_is_preserved_instead_of_being_derived_from_amounts():
    snapshot = normalize_snapshot(
        {
            "records": [
                {
                    "数据类型": "合同/订单",
                    "主题": "DONE101-已完成合同",
                    "所有者": "A",
                    "状态": "结束",
                    "总金额": "100.00",
                    "回款金额": "100",
                    "开票金额": "100.0",
                },
                {
                    "数据类型": "合同/订单",
                    "主题": "OPEN101-未完成合同",
                    "所有者": "A",
                    "状态": "执行中",
                    "总金额": "100.00",
                    "回款金额": "100.00",
                    "开票金额": "99.99",
                },
            ]
        },
        record_id_secret="test",
        timezone="Asia/Shanghai",
    )
    statuses = {row["project_number"]: row["status"] for row in snapshot["records"]}
    assert statuses == {"DONE101": "结束", "OPEN101": "执行中"}


def test_customer_names_drop_the_crm_bracket_pair():
    assert customer_name("〖TPL〗") == "TPL"
    assert customer_name(" 【ZMJ】 ") == "ZMJ"
    assert customer_name("[ABC]") == "ABC"
    assert customer_name("SEY") == "SEY"
    assert customer_name("某公司（上海）") == "某公司（上海）", "brackets inside a name are part of it"
    assert customer_name("〖TPL〗有限公司") == "〖TPL〗有限公司", "only a pair around the whole name is dropped"
    assert customer_name("〖〗") == "〖〗" and customer_name("") is None and customer_name(None) is None


def test_existing_bracketed_customer_names_are_cleaned_on_startup(tmp_path):
    database = Database(tmp_path / "legacy.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO snapshots(id, schema_version, source_updated_at, imported_at, source_contract_count, source_opportunity_count, "
            "output_contract_count, output_opportunity_count, merged_opportunity_count, validation_json) VALUES (1, '1', 'x', 'x', 2, 0, 2, 0, 0, '{}')")
        for record_id, customer in (("r1", "〖TPL〗"), ("r2", "某公司（上海）")):
            connection.execute(
                "INSERT INTO records(snapshot_id, record_id, data_type, subject, customer, owner, updated_at) "
                "VALUES (1, ?, 'contract_order', 'TPL149-合同', ?, 'Li', 'x')", (record_id, customer))
        connection.commit()
    database.initialize()
    with database.connect() as connection:
        names = [row["customer"] for row in connection.execute("SELECT customer FROM records ORDER BY record_id")]
    assert names == ["TPL", "某公司（上海）"]

