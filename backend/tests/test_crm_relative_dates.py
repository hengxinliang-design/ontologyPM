"""2026-11-18: the XToolsCRM list export wrote a contract's 签约时间 as 「昨天」; the project list failed with
「invalid literal for int() with base 10: '昨天'」. Relative dates are converted against the upload day; dates that
cannot be read are left empty and reported; a stored non-date never breaks the derivations."""
import base64
import json
from datetime import date

from app.crm_import import parse_crm_import_base64
from app.project_repository import _signed_year

TODAY = date(2026, 11, 18)


def upload(rows, today=TODAY):
    content = base64.b64encode(json.dumps(rows, ensure_ascii=False).encode("utf-8")).decode()
    return parse_crm_import_base64(content_base64=content, filename="合同订单列表.json", data_type="contract_order", today=today)


def test_relative_and_yearless_dates_become_iso_dates_against_the_upload_day():
    signed = ["昨天", "今天 14:30", "前天", "3天前", "2小时前", "11-16", "12月30日", "2026年1月5日", "2026-10-09"]
    records, summary = upload([{"主题": f"SEY10{i}-合同", "负责人": "Sales A", "签约时间": value} for i, value in enumerate(signed)])
    assert [r["签约时间"] for r in records] == [
        "2026-11-17", "2026-11-18", "2026-11-16", "2026-11-15", "2026-11-18", "2026-11-16",
        "2025-12-30",  # 12月30日 has not come yet this year, so it is last year's
        "2026-01-05", "2026-10-09"]
    assert summary["relative_dates"] == 8 and summary["unreadable_dates"] == 0
    assert summary["relative_date_examples"][0] == "第 2 行签约时间：昨天 → 2026-11-17"


def test_unreadable_dates_are_left_empty_and_reported():
    records, summary = upload([{"主题": "SEY101-合同", "负责人": "Sales A", "签约时间": "待定"},
                               {"主题": "SEY102-合同", "负责人": "Sales A", "签约时间": "2月30日"}])
    assert [r["签约时间"] for r in records] == ["", ""]
    assert summary["unreadable_dates"] == 2
    assert summary["unreadable_date_examples"] == ["第 2 行签约时间：待定", "第 3 行签约时间：2月30日"]


def test_the_fallback_record_id_does_not_change_when_the_same_file_is_uploaded_on_the_day():
    first, _ = upload([{"主题": "SEY101-合同", "负责人": "Sales A", "签约时间": "昨天"}])
    again, _ = upload([{"主题": "SEY101-合同", "负责人": "Sales A", "签约时间": "2026-11-17"}])
    assert first[0]["crm_internal_id"] == again[0]["crm_internal_id"], "a relative date no longer makes a second record"


def test_a_stored_non_date_has_no_signing_year_instead_of_failing():
    assert _signed_year({"data_type": "contract_order", "signed_at": "昨天"}) is None
    assert _signed_year({"data_type": "contract_order", "signed_at": "2026-11-17"}) == 2026


def test_the_project_code_is_read_from_the_start_of_the_subject():
    """User 2026-11-18: the export has no code column; 「HJA114-……补充协议第3号」 is project HJA114, and its contracts group."""
    from app.crm_import import _project_number
    assert _project_number("HJA114-鸿基伟业（苏州）汽车零部件有限公司美国工厂ERP项目补充协议第3号") == "HJA114"
    assert _project_number("GAC13A-广汽颜色件BOP改造项目") == "GAC13A"
    assert _project_number("HJA114_补充协议") == _project_number("HJA114：补充协议") == _project_number("HJA114 补充协议") == "HJA114"
    assert _project_number("鸿基伟业 HJA114-补充协议") is None, "only a code at the start counts"
    assert _project_number("2026年度维护-HJA") is None
    records, summary = upload([{"主题": "HJA114-美国工厂ERP项目", "负责人": "Tao Jun", "签约时间": "2026-03-01"},
                               {"主题": "HJA114-美国工厂ERP项目补充协议第3号", "负责人": "Tao Jun", "签约时间": "2026-10-10"},
                               {"主题": "无编号的合同", "负责人": "Tao Jun", "签约时间": "2026-10-10"}])
    assert [r.get("项目编号") for r in records] == ["HJA114", "HJA114", None]
    assert summary["project_codes"] == 1 and summary["unmatched_project_rows"] == 1


def test_the_opportunity_list_reads_future_dates_its_update_column_and_amounts_in_thousand_yuan():
    """User 2026-11-18 (XToolsCRM销售机会列表): 预计签单日期 「后天」, the column 「更新日期」, and 预期金额 in 千元 —
    won opportunities are ~1/1000 of their contracts (ZMJ180: 550 vs contract ¥545,352)."""
    rows = [{"ID": "2884", "机会主题": "ZMJ180-合同", "客户": "〖ZMJ〗", "负责人": "Li Qiang", "预期金额": "550", "预计签单日期": "后天",
             "可能性": "1", "状态": "成功", "阶段": "合同签约100%", "更新日期": "今天"},
            {"ID": "2885", "机会主题": "TPL160-合同", "客户": "〖TPL〗", "负责人": "Li Qiang", "预期金额": "1,234.5", "预计签单日期": "3天后",
             "可能性": "0.3", "状态": "跟踪", "阶段": "项目立项50%", "更新日期": "46336"}]
    content = base64.b64encode(json.dumps(rows, ensure_ascii=False).encode("utf-8")).decode()
    records, summary = parse_crm_import_base64(content_base64=content, filename="销售机会.json", data_type="sales_opportunity", today=TODAY)
    assert [r["签约时间"] for r in records] == ["2026-11-20", "2026-11-21"]
    assert [r["总金额"] for r in records] == ["550000.00", "1234500.00"]
    assert summary["thousand_yuan_amounts"] == 2 and "source_updated_at" in summary["recognized_fields"]
    contracts, _ = upload([{"主题": "ZMJ180-合同", "负责人": "Li Qiang", "总金额": "545352", "签约时间": "2026-10-01"}])
    assert contracts[0]["总金额"] == "545352", "contract amounts are already in 元"


def test_a_file_uploaded_under_the_wrong_list_is_refused():
    """User 2026-11-19: the opportunity list uploaded under 合同 / 订单 created 573 fake contracts and overwrote 127
    real ones (the two lists' CRM ID numbers overlap). The columns now decide; nothing is imported."""
    import pytest
    from app.crm_import import CrmImportValidationError
    opportunities = [{"ID": "1823", "机会主题": "DFP12B-ERP数据归档", "客户": "〖DFP〗", "负责人": "Li Qiang", "预期金额": "0.5",
                      "预计签单日期": "2022-08-31", "可能性": "1", "状态": "成功", "阶段": "合同签约100%"}]
    contracts = [{"ID": "1823", "主题": "ZMJ113-开口合同", "对应客户": "〖ZMJ〗", "签约时间": "2026-11-10", "总金额": "24910",
                  "回款金额": "0", "开票金额": "0", "状态": "执行中", "所有者": "Li Qiang"}]
    encode = lambda rows: base64.b64encode(json.dumps(rows, ensure_ascii=False).encode("utf-8")).decode()
    with pytest.raises(CrmImportValidationError, match="像是销售机会列表.*请在「销售机会数据」中上传"):
        parse_crm_import_base64(content_base64=encode(opportunities), filename="TSG-商机.json", data_type="contract_order", today=TODAY)
    with pytest.raises(CrmImportValidationError, match="像是合同 / 订单列表.*请在「合同 / 订单数据」中上传"):
        parse_crm_import_base64(content_base64=encode(contracts), filename="TSG-合同.json", data_type="sales_opportunity", today=TODAY)
    records, _ = parse_crm_import_base64(content_base64=encode(contracts), filename="TSG-合同.json", data_type="contract_order", today=TODAY)
    assert len(records) == 1, "the right list still imports"
