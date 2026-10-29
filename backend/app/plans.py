"""Annual operating plans: maintained department targets (base program).

The department review that compares results with these targets is derived in the ontology.
"""
from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .db import Database


def money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), ROUND_HALF_UP))


class AnnualPlanRepository:
    def __init__(self, database: Database, timezone: str):
        self.database = database
        self.timezone = timezone

    def _now(self) -> str:
        return datetime.now(ZoneInfo(self.timezone)).isoformat(timespec="seconds")

    # Confirmed-revenue and gross-margin targets are paused with person-day economics (ontology 4.0.0);
    # their columns stay in annual_operating_plans so they can be re-enabled.
    PLAN_FIELDS = ("new_contract_target", "collection_target", "capacity_person_days_target",
                   "headcount_target", "utilization_target_percent")

    def list_plans(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            columns = ", ".join(("plan_id", "year", "department", *self.PLAN_FIELDS, "notes", "updated_by", "updated_at"))
            return [dict(r) for r in connection.execute(
                f"SELECT {columns} FROM annual_operating_plans ORDER BY year DESC, department").fetchall()]

    def get_plan(self, year: int, department: str | None = None) -> dict[str, Any] | None:
        plans = [p for p in self.list_plans() if p["year"] == year and (department is None or p["department"] == department)]
        return plans[0] if plans else None

    def upsert_plan(self, year: int, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        if not 2000 <= int(year) <= 2100:
            raise ValueError("year must be between 2000 and 2100")
        department = str(payload.get("department") or "实施交付部").strip()
        current = self.get_plan(int(year), department) or {}
        values = {}
        for field in self.PLAN_FIELDS:
            raw = payload.get(field, current.get(field))
            if raw in (None, ""):
                values[field] = None
                continue
            number = Decimal(str(raw))
            if not number.is_finite() or number < 0:
                raise ValueError(f"{field} must be a non-negative number")
            if field.endswith("_percent") and number > 100:
                raise ValueError(f"{field} must be no more than 100")
            values[field] = money(number)
        plan_id = current.get("plan_id") or f"{int(year)}-{department}"
        with self.database.connect() as connection:
            connection.execute(
                f"""INSERT INTO annual_operating_plans(plan_id, year, department, {', '.join(self.PLAN_FIELDS)}, notes, updated_by, updated_at)
                    VALUES (?, ?, ?, {', '.join('?' for _ in self.PLAN_FIELDS)}, ?, ?, ?)
                    ON CONFLICT(plan_id) DO UPDATE SET {', '.join(f'{f}=excluded.{f}' for f in self.PLAN_FIELDS)},
                        notes=excluded.notes, updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                (plan_id, int(year), department, *(values[f] for f in self.PLAN_FIELDS),
                 payload.get("notes", current.get("notes")), actor, self._now()),
            )
            connection.commit()
        return self.get_plan(int(year), department)
