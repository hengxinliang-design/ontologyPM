from __future__ import annotations

import base64
import csv
import io
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .xlsx_timesheet import parse_xlsx_rows


class CrmImportValidationError(ValueError):
    pass


FIELD_ALIASES = {
    "crm_internal_id": ("crm_internal_id", "record_id", "id", "内部id", "合同id", "合同订单id", "商机id", "机会id"),
    "project_number": ("项目编号", "项目代码", "项目号", "project_number", "projectcode"),
    "subject": ("主题", "合同主题", "合同订单主题", "机会主题", "subject", "名称"),
    "customer": ("对应客户", "客户", "客户名称", "customer"),
    "category": ("分类", "合同分类", "类型", "category"),
    "signed_at": ("签约时间", "签约日期", "合同签订日期", "预计签单日期", "signed_at", "日期"),
    "total_amount": ("总金额", "合同金额", "预期金额", "金额", "total_amount"),
    "received_amount": ("回款金额", "已回款", "received_amount"),
    "invoiced_amount": ("开票金额", "已开票", "invoiced_amount"),
    "owner": ("我方签约人", "所有者", "负责人", "owner", "销售负责人"),
    "probability": ("可能性", "成交可能性", "probability"),
    "sales_stage": ("阶段", "商机阶段", "sales_stage", "stage"),
    "status": ("状态", "商机状态", "status"),
    "source_updated_at": (
        "更新时间", "源更新时间", "最后更新时间", "source_updated_at", "updated_at"
    ),
}


def parse_crm_import_base64(
    *, content_base64: str, filename: str, data_type: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if data_type not in {"contract_order", "sales_opportunity"}:
        raise CrmImportValidationError("data_type must be contract_order or sales_opportunity")
    if "," in content_base64 and content_base64.lstrip().startswith("data:"):
        content_base64 = content_base64.split(",", 1)[1]
    try:
        content = base64.b64decode(content_base64, validate=True)
    except Exception as exc:
        raise CrmImportValidationError("content_base64 is not valid base64") from exc

    suffix = Path(filename).suffix.lower()
    if suffix == ".xlsx":
        rows = _xlsx_rows(content)
    elif suffix == ".csv":
        rows = _csv_rows(content)
    elif suffix == ".json":
        rows = _json_rows(content)
    else:
        raise CrmImportValidationError("only .xlsx, .csv, and .json files are supported")

    records, recognized = _normalize_rows(rows, data_type=data_type)
    unmatched = [record["主题"] for record in records if not record.get("项目编号")]
    return records, {
        "filename": filename,
        "format": suffix.removeprefix("."),
        "parsed_rows": len(rows),
        "importable_rows": len(records),
        "recognized_fields": recognized,
        "unmatched_project_rows": len(unmatched),
        "unmatched_project_subjects": unmatched[:5],
    }


def template_fields(data_type: str) -> dict[str, Any]:
    if data_type not in {"contract_order", "sales_opportunity"}:
        raise CrmImportValidationError("data_type must be contract_order or sales_opportunity")
    required = ["subject", "owner"]
    return {
        "data_type": data_type,
        "required_fields": required,
        "fields": [
            {
                "field": field,
                "required": field in required,
                "accepted_headers": list(aliases),
            }
            for field, aliases in FIELD_ALIASES.items()
        ],
    }


def _xlsx_rows(content: bytes) -> list[dict[str, Any]]:
    rows = parse_xlsx_rows(content)
    return _rows_from_matrix(rows)


def _csv_rows(content: bytes) -> list[dict[str, Any]]:
    text: str | None = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise CrmImportValidationError("CSV must use UTF-8 or GB18030/GBK encoding")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    for raw in reader:
        extras = raw.pop(None, None)
        # Some spreadsheet exports leave currency thousands separators unquoted.
        # Repair the final column rather than silently truncating the amount.
        if extras and reader.fieldnames:
            last_header = reader.fieldnames[-1]
            raw[last_header] = ",".join(
                [str(raw.get(last_header) or ""), *(str(value) for value in extras)]
            ).strip(",")
        if any(value not in (None, "") for value in raw.values()):
            rows.append(dict(raw))
    return rows


def _json_rows(content: bytes) -> list[dict[str, Any]]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CrmImportValidationError("JSON file is invalid") from exc
    if isinstance(payload, dict):
        payload = payload.get("records", payload.get("data", payload))
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise CrmImportValidationError("JSON must be an array of data rows or an object containing records[]")
    return payload


def _rows_from_matrix(rows: list[list[Any]]) -> list[dict[str, Any]]:
    if not rows:
        raise CrmImportValidationError("Excel does not contain rows")
    headers = [str(value).strip() if value is not None else "" for value in rows[0]]
    if not any(headers):
        raise CrmImportValidationError("Excel header row is empty")
    result = []
    for row in rows[1:]:
        item = {headers[index]: row[index] if index < len(row) else None for index in range(len(headers)) if headers[index]}
        if any(value not in (None, "") for value in item.values()):
            result.append(item)
    return result


def _normalize_rows(rows: list[dict[str, Any]], *, data_type: str) -> tuple[list[dict[str, Any]], list[str]]:
    if not rows:
        raise CrmImportValidationError("file does not contain importable rows")
    records: list[dict[str, Any]] = []
    all_recognized: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        values = {_header(key): value for key, value in row.items()}
        item: dict[str, Any] = {"数据类型": "合同/订单" if data_type == "contract_order" else "销售机会"}
        recognized: set[str] = set()
        for field, aliases in FIELD_ALIASES.items():
            value = _first_value(values, aliases)
            if value not in (None, ""):
                recognized.add(field)
                item[_output_key(field)] = _field_value(field, value)
        all_recognized.update(recognized)
        if not any(value not in (None, "") for value in item.values() if value != item["数据类型"]):
            continue
        missing = [field for field in ("subject", "owner") if not item.get(_output_key(field))]
        if missing:
            labels = {"subject": "主题", "owner": "所有者/负责人"}
            raise CrmImportValidationError(f"row {row_number} missing required field(s): {', '.join(labels[name] for name in missing)}")
        project_number = item.get("项目编号") or _project_number(str(item["主题"]))
        if project_number:
            item["项目编号"] = project_number
        if not item.get("crm_internal_id"):
            # A project can legitimately have several contracts/orders (for
            # example, a main contract plus an addendum).  Using only the
            # project number here made those rows share one synthetic CRM ID,
            # so a later row overwrote the earlier one during incremental
            # import.  Keep the fallback stable for re-imports while making it
            # specific enough for the source row.
            identity = "|".join(
                (
                    data_type,
                    str(project_number or ""),
                    str(item.get("主题") or ""),
                    str(item.get("签约时间") or ""),
                )
            )
            item["crm_internal_id"] = f"manual:{identity}"
        item["__present_fields"] = sorted(recognized)
        records.append(item)
    if not records:
        raise CrmImportValidationError("file does not contain importable rows")
    return records, sorted(all_recognized)


def _header(value: Any) -> str:
    return re.sub(r"[\s_\-—–/（）()【】\[\]：:]", "", str(value or "")).lower()


def _first_value(values: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        value = values.get(_header(alias))
        if value not in (None, ""):
            return value
    return None


def _output_key(field: str) -> str:
    return {
        "crm_internal_id": "crm_internal_id",
        "project_number": "项目编号",
        "subject": "主题",
        "customer": "对应客户",
        "category": "分类",
        "signed_at": "签约时间",
        "total_amount": "总金额",
        "received_amount": "回款金额",
        "invoiced_amount": "开票金额",
        "owner": "所有者",
        "probability": "可能性",
        "sales_stage": "阶段",
        "status": "状态",
        "source_updated_at": "源更新时间",
    }[field]


def _clean(value: Any) -> str:
    return str(value).strip()


def _field_value(field: str, value: Any) -> str:
    if field in {"signed_at", "source_updated_at"}:
        return _date_value(value)
    if field == "probability":
        return _probability_value(value)
    return _clean(value)


def _date_value(value: Any) -> str:
    text = _clean(value)
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        serial = float(text)
        # Excel dates in the CRM export fall safely inside this range. Avoid
        # treating ordinary numeric IDs as dates when a template is customized.
        if 20_000 <= serial <= 80_000:
            return (date(1899, 12, 30) + timedelta(days=int(serial))).isoformat()
    match = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    return text


def _probability_value(value: Any) -> str:
    text = _clean(value)
    if text.endswith("%"):
        return text
    try:
        number = float(text)
    except ValueError:
        return text
    if 0 <= number <= 1:
        return f"{number * 100:g}%"
    return f"{number:g}%"


def _project_number(subject: str) -> str | None:
    match = re.match(r"^\s*([A-Z]{2,6}\d{2,4}[A-Z]?)\s*[-–—－]", subject)
    return match.group(1) if match else None
