from __future__ import annotations

import base64
import hashlib
import hmac
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_PATTERN = re.compile(r"^\s*([A-Z]{2,6}\d{2,4}[A-Z]?)\s*[-–—－]")


class SnapshotValidationError(ValueError):
    pass


def normalize_snapshot(
    payload: dict[str, Any], *, record_id_secret: str, timezone: str
) -> dict[str, Any]:
    records, updated_at = normalize_records(
        payload, record_id_secret=record_id_secret, timezone=timezone
    )
    _validate_required(records)

    source_stats = _source_stats(payload, records)
    merged_records, merge_result = _merge_converted_opportunities(records)

    # A previously normalized 2.2 snapshot may already have removed converted rows.
    prior_merge = payload.get("conversion_merge") or {}
    if not merge_result["merged_opportunities"] and prior_merge.get("updated_contract_rows"):
        expected = int(prior_merge["updated_contract_rows"])
        candidates = [
            r
            for r in merged_records
            if r["data_type"] == "contract_order"
            and r.get("probability") == 100
            and r.get("status") == "成功"
        ]
        if len(candidates) == expected:
            for record in candidates:
                record["merged_from_opportunity"] = True
            merge_result["merged_opportunities"] = expected

    output_stats = {
        "contract_order": sum(r["data_type"] == "contract_order" for r in merged_records),
        "sales_opportunity": sum(
            r["data_type"] == "sales_opportunity" for r in merged_records
        ),
    }
    duplicate_record_ids = len(merged_records) - len(
        {record["record_id"] for record in merged_records}
    )
    cross_type_duplicates = _cross_type_duplicate_count(merged_records)
    validation = {
        "complete": duplicate_record_ids == 0 and cross_type_duplicates == 0,
        "missing_required_values": 0,
        "duplicate_internal_ids": duplicate_record_ids,
        "cross_type_duplicate_project_ids": cross_type_duplicates,
        "merged_opportunities": merge_result["merged_opportunities"],
    }
    if not validation["complete"]:
        raise SnapshotValidationError(f"snapshot validation failed: {validation}")

    return {
        "schema_version": "2.2",
        "updated_at": updated_at,
        "imported_at": datetime.now(ZoneInfo(timezone)).isoformat(timespec="seconds"),
        "source_stats": source_stats,
        "output_stats": output_stats,
        "validation": validation,
        "records": merged_records,
    }


def normalize_records(
    payload: dict[str, Any], *, record_id_secret: str, timezone: str
) -> tuple[list[dict[str, Any]], str]:
    """Normalize source rows without applying cross-record conversion rules."""
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise SnapshotValidationError("source snapshot must contain records[]")

    updated_at = payload.get("更新时间") or payload.get("updated_at")
    if not updated_at:
        updated_at = datetime.now(ZoneInfo(timezone)).isoformat(timespec="seconds")

    records = [
        _normalize_record(item, updated_at=updated_at, record_id_secret=record_id_secret)
        for item in raw_records
    ]
    _validate_required(records)
    return records, updated_at


def _normalize_record(
    item: dict[str, Any], *, updated_at: str, record_id_secret: str
) -> dict[str, Any]:
    chinese = "数据类型" in item
    raw_type = item.get("数据类型") if chinese else item.get("data_type")
    data_type = {
        "合同/订单": "contract_order",
        "销售机会": "sales_opportunity",
        "contract_order": "contract_order",
        "sales_opportunity": "sales_opportunity",
    }.get(raw_type)
    if not data_type:
        raise SnapshotValidationError(f"unsupported data type: {raw_type!r}")

    subject = _text(item.get("主题") if chinese else item.get("subject"))
    owner = _text(item.get("所有者") if chinese else item.get("owner"))
    project_number = _nullable_text(
        item.get("项目编号") if chinese else item.get("project_number")
    ) or _extract_project_number(subject)
    signed_at = _nullable_text(item.get("签约时间") if chinese else item.get("signed_at"))
    probability = _probability(item.get("可能性") if chinese else item.get("probability"))
    status = _nullable_text(item.get("状态") if chinese else item.get("status"))

    identity = item.get("crm_internal_id") or item.get("record_id")
    if not identity:
        identity = "|".join([data_type, project_number or "", subject, signed_at or ""])
    record_id = item.get("record_id") or _record_id(
        str(identity), secret=record_id_secret
    )

    return {
        "record_id": record_id,
        "data_type": data_type,
        "project_number": project_number,
        "subject": subject,
        "customer": _nullable_text(
            item.get("对应客户") if chinese else item.get("customer")
        ),
        "category": _nullable_text(item.get("分类") if chinese else item.get("category")),
        "signed_at": signed_at,
        "total_amount": _money(item.get("总金额") if chinese else item.get("total_amount")),
        "received_amount": _money(
            item.get("回款金额") if chinese else item.get("received_amount")
        ),
        "invoiced_amount": _money(
            item.get("开票金额") if chinese else item.get("invoiced_amount")
        ),
        "owner": owner,
        "probability": probability,
        "status": status,
        "merged_from_opportunity": bool(item.get("merged_from_opportunity", False)),
        "updated_at": updated_at,
    }


def _merge_converted_opportunities(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    contracts: dict[str, list[dict[str, Any]]] = {}
    opportunities: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        project = record.get("project_number")
        if not project:
            continue
        target = contracts if record["data_type"] == "contract_order" else opportunities
        target.setdefault(project, []).append(record)

    matches = sorted(set(contracts) & set(opportunities))
    anomalies = []
    for project in matches:
        contract_rows = contracts[project]
        opportunity_rows = opportunities[project]
        if (
            len(contract_rows) != 1
            or len(opportunity_rows) != 1
            or opportunity_rows[0].get("probability") != 100
            or opportunity_rows[0].get("status") != "成功"
        ):
            anomalies.append(project)
    if anomalies:
        raise SnapshotValidationError(
            "ambiguous or non-successful contract conversion: " + ", ".join(anomalies)
        )

    matched = set(matches)
    output = []
    for record in records:
        project = record.get("project_number")
        if record["data_type"] == "sales_opportunity" and project in matched:
            continue
        if record["data_type"] == "contract_order" and project in matched:
            record = dict(record)
            record["probability"] = 100
            record["status"] = "成功"
            record["merged_from_opportunity"] = True
        output.append(record)
    return output, {"merged_opportunities": len(matches)}


def _cross_type_duplicate_count(records: list[dict[str, Any]]) -> int:
    contracts = {
        r["project_number"]
        for r in records
        if r["data_type"] == "contract_order" and r.get("project_number")
    }
    opportunities = {
        r["project_number"]
        for r in records
        if r["data_type"] == "sales_opportunity" and r.get("project_number")
    }
    return len(contracts & opportunities)


def _source_stats(
    payload: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, int]:
    sources = payload.get("sources") or {}
    contract_source = sources.get("合同/订单") or sources.get("contract_order") or {}
    opportunity_source = (
        sources.get("销售机会") or sources.get("sales_opportunity") or {}
    )
    return {
        "contract_order": int(
            contract_source.get("total_records")
            or sum(r["data_type"] == "contract_order" for r in records)
        ),
        "sales_opportunity": int(
            opportunity_source.get("total_records")
            or sum(r["data_type"] == "sales_opportunity" for r in records)
        ),
    }


def _validate_required(records: list[dict[str, Any]]) -> None:
    for index, record in enumerate(records):
        for field in ("record_id", "data_type", "subject", "owner"):
            if not record.get(field):
                raise SnapshotValidationError(
                    f"records[{index}].{field} is required"
                )


def _record_id(identity: str, *, secret: str) -> str:
    digest = hmac.new(secret.encode(), identity.encode(), hashlib.sha256).digest()[:18]
    return "rec_" + base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _extract_project_number(subject: str) -> str | None:
    match = PROJECT_PATTERN.match(subject)
    return match.group(1) if match else None


def _probability(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = value.strip().removesuffix("%")
    number = int(value)
    if not 0 <= number <= 100:
        raise SnapshotValidationError("probability must be between 0 and 100")
    return number


def _money(value: Any) -> dict[str, str] | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        amount = Decimal(str(value["amount"])).quantize(Decimal("0.01"))
        display = value.get("display") or f"￥{amount:,.2f}"
        return {"amount": f"{amount:.2f}", "currency": "CNY", "display": display}
    display = str(value).strip()
    plain = display.replace("￥", "").replace(",", "").strip()
    try:
        amount = Decimal(plain).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise SnapshotValidationError(f"invalid money value: {value!r}") from exc
    return {"amount": f"{amount:.2f}", "currency": "CNY", "display": display}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _nullable_text(value: Any) -> str | None:
    text = _text(value)
    return text or None
