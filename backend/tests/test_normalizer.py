from app.normalizer import SnapshotValidationError, normalize_snapshot


def test_merges_successful_opportunity_into_contract():
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
    assert len(snapshot["records"]) == 1
    record = snapshot["records"][0]
    assert record["data_type"] == "contract_order"
    assert record["probability"] == 100
    assert record["status"] == "成功"
    assert record["merged_from_opportunity"] is True


def test_rejects_ambiguous_cross_type_project():
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
    try:
        normalize_snapshot(payload, record_id_secret="test", timezone="America/Detroit")
    except SnapshotValidationError as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("ambiguous conversion must fail")
