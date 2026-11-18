"""CRM receipt changes (requirements baseline D68, ontology 8.11.0).

XToolsCRM gives each contract's cumulative 回款金额 but no receipt dates, so operating revenue (D22) could only come
from 回款节点 confirmed in this system. Each contract upload now records how a contract's 回款金额 changed since it was
last seen, dated the upload day (user 2026-11-18, 「Track CRM changes」):

* first sighting of a contract signed in the upload's year with money received: an opening estimate (期初估算), counted
  in the signing month — the receipts are assumed to have arrived this year;
* first sighting otherwise (signed earlier, or nothing received yet): the baseline (起点), not revenue;
* a later upload with a different 回款金额: a change (变动) on the upload day, negative when CRM went down.

Revenue uses these rows only for projects without any confirmed 回款节点 (those keep D22), so nothing is counted twice.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

KINDS = ("opening_estimate", "baseline", "change")


def _amount(value: Any) -> Decimal:
    if isinstance(value, dict):
        value = value.get("amount")
    try:
        return Decimal(str(value)) if value not in (None, "") else Decimal("0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _iso_date(value: Any) -> str | None:
    text = str(value or "")
    return text[:10] if len(text) >= 10 and text[4] == "-" and text[7] == "-" and text[:4].isdigit() else None


def record_contract_receipts(database: Any, contracts: Iterable[dict[str, Any]], observed_on: date,
                             import_log_id: int | None = None) -> dict[str, Any]:
    """Writes one ledger row per contract seen for the first time or whose 回款金额 changed; returns what was written."""
    observed = observed_on.isoformat()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = {"opening_estimates": 0, "baselines": 0, "changes": 0, "opening_estimate_amount": Decimal("0"),
               "change_amount": Decimal("0")}
    with database.connect() as connection:
        last = {row["record_id"]: Decimal(row["received_amount"]) for row in connection.execute(
            """SELECT record_id, received_amount FROM crm_receipt_changes AS c
               WHERE rowid = (SELECT MAX(rowid) FROM crm_receipt_changes WHERE record_id = c.record_id)""")}
        for contract in contracts:
            if contract.get("data_type") != "contract_order":
                continue
            amount = _amount(contract.get("received_amount"))
            previous = last.get(contract["record_id"])
            signed = _iso_date(contract.get("signed_at"))
            if previous is None:
                if signed and signed[:4] == observed[:4] and amount != 0:
                    kind, change, revenue_date = "opening_estimate", amount, min(signed, observed)
                    written["opening_estimates"] += 1
                    written["opening_estimate_amount"] += amount
                else:
                    kind, change, revenue_date = "baseline", amount, None
                    written["baselines"] += 1
            else:
                change = amount - previous
                if change == 0:
                    continue
                kind, revenue_date = "change", observed
                written["changes"] += 1
                written["change_amount"] += change
            connection.execute(
                """INSERT INTO crm_receipt_changes(change_id, record_id, project_number, subject, signed_at, observed_on,
                       previous_amount, received_amount, change_amount, kind, revenue_date, import_log_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"crc_{uuid.uuid4().hex[:16]}", contract["record_id"], contract.get("project_number"), contract.get("subject"),
                 signed, observed, None if previous is None else format(previous, "f"), format(amount, "f"),
                 format(change, "f"), kind, revenue_date, import_log_id, now))
            last[contract["record_id"]] = amount
        connection.commit()
    return {**written, "opening_estimate_amount": format(written["opening_estimate_amount"], ".2f"),
            "change_amount": format(written["change_amount"], ".2f")}


def backfill_from_active_contracts(database: Any, records: Any, fallback_day: date) -> dict[str, Any] | None:
    """Once, when the ledger is empty but contracts were already uploaded (before 8.11.0): their current 回款金额 is the
    first sighting, dated the day of the first contract upload."""
    with database.connect() as connection:
        if connection.execute("SELECT 1 FROM crm_receipt_changes LIMIT 1").fetchone():
            return None
        first = connection.execute(
            "SELECT MIN(imported_at) AS first FROM crm_import_logs WHERE data_type = 'contract_order'").fetchone()
    if not first or not first["first"]:
        return None  # only contracts that came in through the upload page (not the example snapshot) are tracked
    snapshot = records.export_records()
    contracts = [r for r in (snapshot or {}).get("records", []) if r.get("data_type") == "contract_order"]
    if not contracts:
        return None
    observed = date.fromisoformat(first["first"][:10]) if first["first"][:4].isdigit() else fallback_day
    return record_contract_receipts(database, contracts, observed)


def revenue_rows(database: Any, year: int, period_end: str) -> list[dict[str, Any]]:
    """Ledger rows that count as revenue in the year up to the period end (opening estimates and changes)."""
    with database.connect() as connection:
        return [dict(row) for row in connection.execute(
            """SELECT project_number, kind, change_amount, revenue_date FROM crm_receipt_changes
               WHERE kind IN ('opening_estimate', 'change') AND substr(revenue_date, 1, 4) = ? AND revenue_date <= ?""",
            (str(year), period_end))]
