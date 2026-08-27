from app.normalizer import SnapshotValidationError, normalize_snapshot


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
    assert contract["status"] == "未完成"
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
    assert all(row["status"] == "未完成" for row in contracts)
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


def test_contract_status_is_derived_from_total_received_and_invoiced_amounts():
    snapshot = normalize_snapshot(
        {
            "records": [
                {
                    "数据类型": "合同/订单",
                    "主题": "DONE101-已完成合同",
                    "所有者": "A",
                    "总金额": "100.00",
                    "回款金额": "100",
                    "开票金额": "100.0",
                },
                {
                    "数据类型": "合同/订单",
                    "主题": "OPEN101-未完成合同",
                    "所有者": "A",
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
    assert statuses == {"DONE101": "已完成", "OPEN101": "未完成"}
