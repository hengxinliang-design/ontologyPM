"""AI suggestions written back after a person approves them (requirements baseline D51-D55, ontology 8.6.0).

``AiFindingRepository`` is the base program: it stores suggestions (``ai_proposals``), approved AI risk flags
(``ai_risk_flags``) and AI strategy notes (``ai_strategy_notes``). Only ontology actions call its write methods,
except ``AiFindingsService.refresh``: the hourly recheck run by the system (D54), which closes or expires items
whose cited facts no longer hold or whose time is up, and records the result of every check.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .db import Database

logger = logging.getLogger(__name__)
FINDING_TABLES = {"riskFlag": ("ai_risk_flags", "flag_id"), "strategyNote": ("ai_strategy_notes", "note_id")}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AiFindingRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    # ------------------------------------------------------------ reads
    def rows(self, table: str, order_by: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order_by}").fetchall()]

    def proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM ai_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------ writes (called by ontology actions)
    def submit(self, fields: dict[str, Any], actor: str, expires_on: date) -> tuple[dict[str, Any], list[str]]:
        """A new pending suggestion; a pending one with the same dedupe key is superseded (D54). Returns the row and
        the superseded ids."""
        proposal_id = "aip_" + uuid.uuid4().hex[:16]
        now = _now()
        with self.database.connect() as connection:
            superseded = [row["proposal_id"] for row in connection.execute(
                "SELECT proposal_id FROM ai_proposals WHERE status = 'pending' AND dedupe_key = ?", (fields["dedupe_key"],))]
            connection.execute(
                "UPDATE ai_proposals SET status = 'superseded', decided_by = ?, decided_at = ?, "
                "decision_note = ? WHERE status = 'pending' AND dedupe_key = ?",
                (actor, now, f"同一项目、同类、引用相同依据的提案再次提交，已由新提案 {proposal_id} 替换；本条未经审批，与依据复核无关", fields["dedupe_key"]))
            connection.execute(
                """INSERT INTO ai_proposals(proposal_id, kind, project_number, category, proposed_category, title, detail,
                       basis_json, parameters_json, dedupe_key, source, source_ref, status, submitted_by, submitted_at,
                       expires_on, basis_holds, basis_note, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, 1, ?, ?)""",
                (proposal_id, fields["kind"], fields.get("project_number"), fields.get("category"),
                 fields.get("proposed_category"), fields["title"],
                 fields.get("detail"), json.dumps(fields["basis"], ensure_ascii=False),
                 json.dumps(fields.get("parameters") or {}, ensure_ascii=False), fields["dedupe_key"],
                 fields.get("source") or "assistant", fields.get("source_ref"), actor, now, expires_on.isoformat(),
                 fields.get("basis_note"), now))
            connection.commit()
        return self.proposal(proposal_id) or {}, superseded

    def decide(self, proposal_id: str, status: str, actor: str, note: str | None, result_id: str | None = None) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE ai_proposals SET status = ?, decided_by = ?, decided_at = ?, decision_note = ?, result_id = ? "
                "WHERE proposal_id = ?", (status, actor, _now(), note, result_id, proposal_id))
            connection.commit()

    def create_finding(self, kind: str, proposal: dict[str, Any], actor: str, valid_until: date,
                       basis_note: str | None) -> tuple[str, list[str]]:
        """An approved risk flag or strategy note; an active one with the same dedupe key is superseded (D54)."""
        table, key = FINDING_TABLES[kind]
        finding_id = ("arf_" if kind == "riskFlag" else "asn_") + uuid.uuid4().hex[:16]
        now = _now()
        with self.database.connect() as connection:
            superseded = [row[key] for row in connection.execute(
                f"SELECT {key} FROM {table} WHERE status = 'active' AND dedupe_key = ?", (proposal["dedupe_key"],))]
            connection.execute(
                f"UPDATE {table} SET status = 'superseded', closed_by = ?, closed_at = ?, closed_reason = ? "
                "WHERE status = 'active' AND dedupe_key = ?",
                (actor, now, f"同类、引用相同依据的新结论 {finding_id} 已批准，替换本条", proposal["dedupe_key"]))
            if kind == "riskFlag":
                connection.execute(
                    """INSERT INTO ai_risk_flags(flag_id, project_number, title, detail, basis_json, dedupe_key, proposal_id,
                           status, approved_by, approved_at, valid_until, basis_holds, basis_note, checked_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, 1, ?, ?)""",
                    (finding_id, proposal["project_number"], proposal["title"], proposal.get("detail"), proposal["basis_json"],
                     proposal["dedupe_key"], proposal["proposal_id"], actor, now, valid_until.isoformat(), basis_note, now))
            else:
                connection.execute(
                    """INSERT INTO ai_strategy_notes(note_id, category, proposed_category, title, detail, basis_json,
                           dedupe_key, proposal_id, status, approved_by, approved_at, valid_until, basis_holds, basis_note,
                           checked_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, 1, ?, ?)""",
                    (finding_id, proposal.get("category") or "other", proposal.get("proposed_category"), proposal["title"],
                     proposal.get("detail"),
                     proposal["basis_json"], proposal["dedupe_key"], proposal["proposal_id"], actor, now,
                     valid_until.isoformat(), basis_note, now))
            connection.commit()
        return finding_id, superseded

    def renew(self, kind: str, finding_id: str, valid_until: date, basis_note: str | None) -> None:
        table, key = FINDING_TABLES[kind]
        with self.database.connect() as connection:
            connection.execute(
                f"UPDATE {table} SET valid_until = ?, basis_holds = 1, basis_note = ?, checked_at = ? WHERE {key} = ?",
                (valid_until.isoformat(), basis_note, _now(), finding_id))
            connection.commit()

    def close(self, kind: str, finding_id: str, actor: str, reason: str, status: str = "closed") -> None:
        table, key = FINDING_TABLES[kind]
        with self.database.connect() as connection:
            connection.execute(
                f"UPDATE {table} SET status = ?, closed_by = ?, closed_at = ?, closed_reason = ? WHERE {key} = ?",
                (status, actor, _now(), reason, finding_id))
            connection.commit()

    # ------------------------------------------------------------ the hourly recheck (system)
    def record_check(self, table: str, key: str, item_id: str, holds: bool, note: str) -> None:
        with self.database.connect() as connection:
            row = connection.execute(f"SELECT basis_holds, basis_note FROM {table} WHERE {key} = ?", (item_id,)).fetchone()
            # Only write when the result changed: an unchanged hourly check must not invalidate the object index.
            if row is not None and (bool(row["basis_holds"]) != holds or (row["basis_note"] or "") != note):
                connection.execute(f"UPDATE {table} SET basis_holds = ?, basis_note = ?, checked_at = ? WHERE {key} = ?",
                                   (int(holds), note, _now(), item_id))
                connection.commit()

    def expire_proposal(self, proposal_id: str, reason: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE ai_proposals SET status = 'expired', decided_by = 'system', decided_at = ?, decision_note = ? "
                "WHERE proposal_id = ? AND status = 'pending'", (_now(), reason, proposal_id))
            connection.commit()


class AiFindingsService:
    """D54: every hour, recheck the facts of pending suggestions, active flags and notes, and interventions opened
    from a suggestion; close, expire or mark them. Runs as the system on the full (unscoped) ontology."""

    def __init__(self, repository: AiFindingRepository, ontology_service: Any) -> None:
        self.repository = repository
        self.ontology_service = ontology_service

    def refresh(self, today: date | None = None) -> dict[str, int]:
        from .ontology.derivations import ai_basis
        from .ontology.rules import ai_lookups

        ctx = self.ontology_service.context(self.ontology_service.SYSTEM_USER)
        today = today or ctx.projects.today()
        lookups = ai_lookups(ctx)
        counts = {"checked": 0, "expired": 0, "closed": 0}

        def verdict(basis_json: str) -> tuple[bool, str]:
            try:
                facts = ai_basis.normalize(json.loads(basis_json))
            except (ai_basis.BasisError, json.JSONDecodeError) as exc:
                return False, f"依据无法读取：{exc}"
            return ai_basis.summary(ai_basis.check(ctx, facts, lookups))

        open_interventions = {row["intervention_id"] for row in self.repository.rows("interventions", "intervention_id")
                              if row["status"] != "closed"}
        for proposal in self.repository.rows("ai_proposals", "submitted_at"):
            watched = proposal["status"] == "pending" or (
                proposal["status"] == "approved" and proposal["kind"] == "intervention" and proposal["result_id"] in open_interventions)
            if not watched:
                continue
            counts["checked"] += 1
            holds, note = verdict(proposal["basis_json"])
            self.repository.record_check("ai_proposals", "proposal_id", proposal["proposal_id"], holds, note)
            if proposal["status"] != "pending":
                continue  # an intervention opened from it is only marked; a person closes it (D54)
            if proposal["expires_on"] < today.isoformat():
                self.repository.expire_proposal(proposal["proposal_id"], f"超过待批天数，{proposal['expires_on']} 起过期")
                counts["expired"] += 1
            elif not holds:
                self.repository.expire_proposal(proposal["proposal_id"], note)
                counts["expired"] += 1
        for kind, (table, key) in FINDING_TABLES.items():
            for finding in self.repository.rows(table, key):
                if finding["status"] != "active":
                    continue
                counts["checked"] += 1
                holds, note = verdict(finding["basis_json"])
                self.repository.record_check(table, key, finding[key], holds, note)
                if finding["valid_until"] < today.isoformat():
                    self.repository.close(kind, finding[key], "system", f"有效期到 {finding['valid_until']}，已过期", "expired")
                    counts["expired"] += 1
                elif not holds:
                    self.repository.close(kind, finding[key], "system", note)
                    counts["closed"] += 1
        if counts["expired"] or counts["closed"]:
            logger.info("AI findings recheck: %s", counts)
        return counts


def valid_until(today: date, days: int) -> date:
    return today + timedelta(days=days)
