from __future__ import annotations

import base64
import hashlib
import io
import re
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree as ET


NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
LEGACY_REQUIRED_HEADERS = {
    "姓名",
    "日期",
    "项目代码",
    "天数",
    "工作内容",
    "流程状态",
}
EXPORTED_DETAIL_REQUIRED_HEADERS = {
    "姓名",
    "项目代码",
    "时间",
    "天数",
    "加班天数",
    "工作内容",
}


def parse_timesheet_xlsx_base64(content_base64: str) -> list[dict[str, Any]]:
    if "," in content_base64 and content_base64.lstrip().startswith("data:"):
        content_base64 = content_base64.split(",", 1)[1]
    try:
        workbook_bytes = base64.b64decode(content_base64, validate=True)
    except Exception as exc:
        raise ValueError("content_base64 is not valid base64") from exc
    return parse_timesheet_xlsx(workbook_bytes)


def parse_timesheet_xlsx(workbook_bytes: bytes) -> list[dict[str, Any]]:
    selected = _find_timesheet_sheet(parse_xlsx_sheets(workbook_bytes))
    if selected is None:
        raise ValueError(
            "Excel missing required columns: "
            "姓名, 项目代码, 日期/时间, 天数, 加班天数(导出明细), 工作内容"
        )
    rows, header_row, source_kind = selected
    headers = [str(value).strip() if value is not None else "" for value in rows[header_row]]
    indexes = {header: headers.index(header) for header in headers if header}

    entries: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows[header_row + 1 :], start=header_row + 2):
        raw_name = _value(row, indexes["姓名"])
        raw_project = _value(row, indexes["项目代码"])
        raw_date = _value(row, indexes["日期"] if source_kind == "legacy" else indexes["时间"])
        raw_days = _value(row, indexes["天数"])
        raw_overtime_days = _value(row, indexes["加班天数"]) if source_kind == "exported_detail" else None
        raw_content = _value(row, indexes["工作内容"])
        raw_status = _value(row, indexes["流程状态"]) if source_kind == "legacy" else "流转完成"
        if not any([raw_name, raw_project, raw_date, raw_days, raw_overtime_days, raw_content, raw_status]):
            continue
        if not all([raw_name, raw_project, raw_date, raw_content]):
            raise ValueError(f"row {row_number} missing required timesheet values")

        employee_name = str(raw_name).strip()
        project_number = str(raw_project).strip()
        work_date = _date_text(raw_date)
        work_days = _days(raw_days, row_number, "天数")
        overtime_days = _days(raw_overtime_days, row_number, "加班天数")
        if work_days + overtime_days <= 0:
            raise ValueError(f"row {row_number} requires 天数 or 加班天数")
        hours = work_days * Decimal("8")
        overtime_hours = overtime_days * Decimal("8")
        if hours + overtime_hours > 24:
            raise ValueError(f"row {row_number} total hours must be between 0 and 24")
        approval_status = _approval_status(str(raw_status or "").strip())
        approved_hours = hours + overtime_hours if approval_status == "approved" else Decimal("0")
        work_content = str(raw_content or "").strip()
        # Export display formats are not stable identifiers: the same entry can
        # be re-exported as ``1`` or ``1.0`` and a workflow state can change
        # after the work itself was submitted.  Those representation changes
        # must not create a second worktime row on re-import.
        identity_parts = [
            employee_name,
            project_number,
            work_date,
            _identity_number(work_days),
            _identity_number(overtime_days),
            work_content,
        ]
        entries.append(
            {
                "external_id": _external_id(*identity_parts),
                "project_number": project_number,
                "employee_id": _employee_id(employee_name),
                "employee_name": employee_name,
                "work_date": work_date,
                "hours": f"{hours:.4f}",
                "overtime_hours": f"{overtime_hours:.4f}",
                "approved_hours": f"{approved_hours:.4f}",
                "approval_status": approval_status,
                "work_content": work_content,
                "source_updated_at": f"{work_date}T00:00:00+08:00",
            }
        )
    if not entries:
        raise ValueError("Excel does not contain importable timesheet rows")
    return entries


def parse_xlsx_rows(workbook_bytes: bytes) -> list[list[Any]]:
    with zipfile.ZipFile(io.BytesIO(workbook_bytes)) as archive:
        shared_strings = _shared_strings(archive)
        sheet_xml = archive.read(_first_sheet_path(archive))
    return _sheet_rows(sheet_xml, shared_strings)


def parse_xlsx_sheets(workbook_bytes: bytes) -> list[list[list[Any]]]:
    with zipfile.ZipFile(io.BytesIO(workbook_bytes)) as archive:
        shared_strings = _shared_strings(archive)
        paths = sorted(
            name for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        return [_sheet_rows(archive.read(path), shared_strings) for path in paths]


def _find_timesheet_sheet(
    sheets: list[list[list[Any]]],
) -> tuple[list[list[Any]], int, str] | None:
    for rows in sheets:
        for index, row in enumerate(rows):
            headers = {str(value).strip() for value in row if value is not None}
            if LEGACY_REQUIRED_HEADERS <= headers:
                return rows, index, "legacy"
            if EXPORTED_DETAIL_REQUIRED_HEADERS <= headers:
                return rows, index, "exported_detail"
    return None


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        xml = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(xml)
    strings = []
    for item in root.findall("a:si", NS):
        strings.append("".join(text.text or "" for text in item.findall(".//a:t", NS)))
    return strings


def _first_sheet_path(archive: zipfile.ZipFile) -> str:
    if "xl/worksheets/sheet1.xml" in archive.namelist():
        return "xl/worksheets/sheet1.xml"
    for name in archive.namelist():
        if name.startswith("xl/worksheets/") and name.endswith(".xml"):
            return name
    raise ValueError("Excel does not contain worksheets")


def _sheet_rows(sheet_xml: bytes, shared_strings: list[str]) -> list[list[Any]]:
    root = ET.fromstring(sheet_xml)
    rows: list[list[Any]] = []
    for row in root.findall(".//a:sheetData/a:row", NS):
        values: dict[int, Any] = {}
        for cell in row.findall("a:c", NS):
            ref = cell.attrib.get("r", "")
            column_index = _column_index(ref)
            values[column_index] = _cell_value(cell, shared_strings)
        if values:
            max_index = max(values)
            rows.append([values.get(index) for index in range(max_index + 1)])
    return rows


def _column_index(ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", ref.upper())
    value = 0
    for letter in letters:
        value = value * 26 + (ord(letter) - ord("A") + 1)
    return value - 1


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//a:t", NS))
    value = cell.find("a:v", NS)
    if value is None or value.text is None:
        return None
    text = value.text
    if cell_type == "s":
        return shared_strings[int(text)]
    return text


def _value(row: list[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _date_text(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}", text):
        return datetime.fromisoformat(text[:10]).date().isoformat()
    if re.match(r"^\d{4}/\d{1,2}/\d{1,2}$", text):
        return datetime.strptime(text, "%Y/%m/%d").date().isoformat()
    if re.match(r"^\d+(\.\d+)?$", text):
        return (date(1899, 12, 30) + timedelta(days=int(float(text)))).isoformat()
    raise ValueError(f"unsupported date value: {text}")


def _approval_status(status: str) -> str:
    if status == "流转完成":
        return "approved"
    if "驳回" in status or "拒绝" in status:
        return "rejected"
    return "submitted"


def _days(value: Any, row_number: int, column_name: str) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        days = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"row {row_number} has invalid {column_name}") from exc
    if days < 0:
        raise ValueError(f"row {row_number} {column_name} must be non-negative")
    return days


def _employee_id(employee_name: str) -> str:
    return f"EMP-{hashlib.sha1(employee_name.encode('utf-8')).hexdigest()[:8].upper()}"


def _identity_number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _external_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16].upper()
    return f"TIMESHEET-XLSX-{digest}"
