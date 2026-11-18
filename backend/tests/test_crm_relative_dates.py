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
