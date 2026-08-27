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
REQUIRED_HEADERS = {
    "姓名",
    "日期",
    "项目代码",
    "天数",
    "工作内容",
    "流程状态",
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
    rows = parse_xlsx_rows(workbook_bytes)
    if not rows:
        raise ValueError("Excel does not contain rows")
    headers = [str(value).strip() if value is not None else "" for value in rows[0]]
    missing = REQUIRED_HEADERS - set(headers)
    if missing:
        raise ValueError(f"Excel missing required columns: {', '.join(sorted(missing))}")
    indexes = {header: headers.index(header) for header in headers if header}

    entries: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows[1:], start=2):
        raw_name = _value(row, indexes["姓名"])
        raw_project = _value(row, indexes["项目代码"])
        raw_date = _value(row, indexes["日期"])
        raw_days = _value(row, indexes["天数"])
        raw_content = _value(row, indexes["工作内容"])
        raw_status = _value(row, indexes["流程状态"])
        if not any([raw_name, raw_project, raw_date, raw_days, raw_content, raw_status]):
            continue
        if not all([raw_name, raw_project, raw_date, raw_days]):
            raise ValueError(f"row {row_number} missing required timesheet values")

        employee_name = str(raw_name).strip()
        project_number = str(raw_project).strip()
        work_date = _date_text(raw_date)
        hours = Decimal(str(raw_days)) * Decimal("8")
        if hours < 0 or hours > 24:
            raise ValueError(f"row {row_number} hours must be between 0 and 24")
        approval_status = _approval_status(str(raw_status or "").strip())
        approved_hours = hours if approval_status == "approved" else Decimal("0")
        work_content = str(raw_content or "").strip()
        entries.append(
            {
                "external_id": _external_id(
                    employee_name,
                    project_number,
                    work_date,
                    str(raw_days),
                    work_content,
                    str(raw_status or ""),
                ),
                "project_number": project_number,
                "employee_id": _employee_id(employee_name),
                "employee_name": employee_name,
                "work_date": work_date,
                "hours": f"{hours:.4f}",
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


def _employee_id(employee_name: str) -> str:
    return f"EMP-{hashlib.sha1(employee_name.encode('utf-8')).hexdigest()[:8].upper()}"


def _external_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16].upper()
    return f"TIMESHEET-XLSX-{digest}"
