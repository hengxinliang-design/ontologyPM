"""PM Assistant conversation history (requirements baseline D50, which amends D17).

Each user's questions and answers are kept per conversation until that user deletes them. Only the owner can list,
read or delete a conversation; another user's conversation id behaves as if it did not exist. A follow-up in a saved
conversation sends its last ``CONTEXT_MESSAGES`` messages to the model, each cut to ``CONTEXT_CHARS`` characters,
the same context the chat had before history was kept. The audit log (assistant_log) is separate and still keeps no
answers.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from .db import Database

TITLE_CHARS = 40
# AiProposalStatus labels (ontology.yaml); kept here so the history does not depend on the rule registry.
PROPOSAL_STATUS_LABELS = {"pending": "待批准", "approved": "已批准", "rejected": "已驳回", "expired": "已过期", "superseded": "已被取代"}


def _submission(row: Any) -> dict[str, Any]:
    return {"proposalId": row["proposal_id"], "status": row["status"], "statusLabel": PROPOSAL_STATUS_LABELS.get(row["status"], row["status"]),
            "submittedBy": row["submitted_by"], "submittedAt": row["submitted_at"], "expiresOn": row["expires_on"],
            "decidedBy": row["decided_by"], "decidedAt": row["decided_at"], "decisionNote": row["decision_note"]}


def card_ref(message_id: str, index: int) -> str:
    """The source_ref an AI suggestion submitted from a saved answer carries: which answer, which card."""
    return f"{message_id}:{index}"
CONTEXT_MESSAGES = 6
CONTEXT_CHARS = 1200
LIST_LIMIT = 500


class ConversationNotFound(LookupError):
    """No conversation with this id for this user (it may exist for someone else)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner(user: dict[str, Any]) -> str:
    return user.get("username") or "anonymous"


def title_for(question: str) -> str:
    """The conversation's name: its first question on one line, cut to TITLE_CHARS characters."""
    text = re.sub(r"\s+", " ", question).strip()
    return text if len(text) <= TITLE_CHARS else text[: TITLE_CHARS - 1] + "…"


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class AssistantHistory:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self, user: dict[str, Any], query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """The user's conversations, most recently used first; ``query`` matches the title or any message."""
        sql = ("SELECT c.conversation_id, c.title, c.created_at, c.updated_at, "
               "(SELECT COUNT(*) FROM assistant_messages m WHERE m.conversation_id = c.conversation_id) AS message_count "
               "FROM assistant_conversations c WHERE c.owner = ?")
        params: list[Any] = [_owner(user)]
        term = query.strip()
        if term:
            sql += (" AND (c.title LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM assistant_messages m "
                    "WHERE m.conversation_id = c.conversation_id AND m.content LIKE ? ESCAPE '\\'))")
            params += [_like(term), _like(term)]
        sql += " ORDER BY c.updated_at DESC, c.conversation_id DESC LIMIT ?"
        params.append(max(1, min(limit, LIST_LIMIT)))
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [{"conversation_id": row["conversation_id"], "title": row["title"], "created_at": row["created_at"],
                 "updated_at": row["updated_at"], "message_count": row["message_count"]} for row in rows]

    def get(self, user: dict[str, Any], conversation_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            conversation = self._owned(connection, user, conversation_id)
            rows = connection.execute(
                "SELECT * FROM assistant_messages WHERE conversation_id = ? ORDER BY seq", (conversation_id,)
            ).fetchall()
            submissions = self._submissions(connection, [row["message_id"] for row in rows if row["role"] == "assistant"])
            messages = [self._message(row) for row in rows]
            for message in messages:
                for index, suggestion in enumerate(message["suggestions"]):
                    if not isinstance(suggestion, dict) or suggestion.get("actionType") != "submitAiProposal":
                        continue
                    found = submissions.get(card_ref(message["message_id"], index)) or self._unlinked_submission(
                        connection, conversation["owner"], message, suggestion.get("parameters") or {})
                    if found:
                        suggestion["submission"] = found
        return {"conversation_id": conversation["conversation_id"], "title": conversation["title"],
                "created_at": conversation["created_at"], "updated_at": conversation["updated_at"],
                "messages": messages}

    @staticmethod
    def _submissions(connection: Any, message_ids: list[str]) -> dict[str, dict[str, Any]]:
        """What became of the AI suggestion cards in these answers (8.8.0): the latest suggestion each card was submitted
        as, with who submitted it and when, and its decision. A card is matched by its source_ref (message id + index)."""
        if not message_ids:
            return {}
        refs: dict[str, dict[str, Any]] = {}
        marks = ",".join("?" * len(message_ids))
        rows = connection.execute(
            f"SELECT * FROM ai_proposals WHERE substr(source_ref, 1, instr(source_ref, ':') - 1) IN ({marks}) "
            "ORDER BY submitted_at", message_ids).fetchall()
        for row in rows:  # later submissions of the same card replace earlier ones
            refs[row["source_ref"]] = _submission(row)
        return refs

    @staticmethod
    def _unlinked_submission(connection: Any, owner: str, message: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any] | None:
        """Cards submitted before cards were linked (before 8.8.0) carry no source_ref: match the owner's suggestion of the
        same kind and title submitted after the answer."""
        from .ontology.derivations import ai_basis

        if not parameters.get("title"):
            return None

        def facts(raw: Any) -> list[str] | None:
            try:
                return sorted(ai_basis.identity(f) for f in ai_basis.normalize(raw))
            except ai_basis.BasisError:
                return None

        wanted = facts(parameters.get("basis"))
        if wanted is None:
            return None  # a card whose facts cannot be read was never submitted
        rows = connection.execute(
            "SELECT * FROM ai_proposals WHERE (source_ref IS NULL OR source_ref = '' OR instr(source_ref, ':') = 0) "
            "AND submitted_by = ? AND kind = ? AND title = ? AND submitted_at >= ? ORDER BY submitted_at DESC",
            (owner, parameters.get("kind"), str(parameters["title"]).strip(), message["created_at"])).fetchall()
        # Same kind and title is not enough (two cards in one answer can share a title): the cited facts must match too.
        row = next((r for r in rows if facts(json.loads(r["basis_json"])) == wanted), None)
        return _submission(row) if row else None

    def context(self, user: dict[str, Any], conversation_id: str) -> list[dict[str, str]]:
        """What a follow-up sends to the model: the last messages of the conversation, cut to size."""
        with self.database.connect() as connection:
            self._owned(connection, user, conversation_id)
            rows = connection.execute(
                "SELECT role, content FROM assistant_messages WHERE conversation_id = ? ORDER BY seq DESC LIMIT ?",
                (conversation_id, CONTEXT_MESSAGES),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"][:CONTEXT_CHARS]} for row in reversed(rows)]

    def record(self, user: dict[str, Any], conversation_id: str | None, question: str,
               answer: dict[str, Any]) -> dict[str, Any]:
        """Keep one answered question; a new conversation is named after it. Returns the conversation summary."""
        now = _now()
        with self.database.connect() as connection:
            if conversation_id is None:
                conversation_id = "conv_" + uuid.uuid4().hex[:16]
                connection.execute(
                    "INSERT INTO assistant_conversations(conversation_id, owner, title, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)", (conversation_id, _owner(user), title_for(question), now, now))
            else:
                self._owned(connection, user, conversation_id)
                connection.execute("UPDATE assistant_conversations SET updated_at = ? WHERE conversation_id = ?",
                                   (now, conversation_id))
            seq = connection.execute("SELECT COALESCE(MAX(seq), 0) FROM assistant_messages WHERE conversation_id = ?",
                                     (conversation_id,)).fetchone()[0]
            insert = ("INSERT INTO assistant_messages(message_id, conversation_id, seq, role, content, sources_json, "
                      "suggestions_json, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
            connection.execute(insert, ("amsg_" + uuid.uuid4().hex[:16], conversation_id, seq + 1, "user", question,
                                        "[]", "[]", None, now))
            answer_id = "amsg_" + uuid.uuid4().hex[:16]
            connection.execute(insert, (answer_id, conversation_id, seq + 2, "assistant",
                                        answer["answer"], json.dumps(answer.get("sources") or [], ensure_ascii=False),
                                        json.dumps(answer.get("suggestions") or [], ensure_ascii=False, default=str),
                                        answer.get("model"), now))
            connection.commit()
        return {"conversation_id": conversation_id, "updated_at": now, "message_id": answer_id}

    def delete(self, user: dict[str, Any], conversation_id: str) -> None:
        with self.database.connect() as connection:
            self._owned(connection, user, conversation_id)
            connection.execute("DELETE FROM assistant_messages WHERE conversation_id = ?", (conversation_id,))
            connection.execute("DELETE FROM assistant_conversations WHERE conversation_id = ?", (conversation_id,))
            connection.commit()

    def delete_all(self, user: dict[str, Any]) -> int:
        owner = _owner(user)
        with self.database.connect() as connection:
            connection.execute("DELETE FROM assistant_messages WHERE conversation_id IN "
                               "(SELECT conversation_id FROM assistant_conversations WHERE owner = ?)", (owner,))
            deleted = connection.execute("DELETE FROM assistant_conversations WHERE owner = ?", (owner,)).rowcount
            connection.commit()
        return deleted

    @staticmethod
    def _owned(connection: Any, user: dict[str, Any], conversation_id: str) -> Any:
        row = connection.execute(
            "SELECT * FROM assistant_conversations WHERE conversation_id = ? AND owner = ?",
            (conversation_id, _owner(user)),
        ).fetchone()
        if row is None:
            raise ConversationNotFound(conversation_id)
        return row

    @staticmethod
    def _message(row: Any) -> dict[str, Any]:
        return {"message_id": row["message_id"], "role": row["role"], "content": row["content"],
                "sources": json.loads(row["sources_json"]), "suggestions": json.loads(row["suggestions_json"]),
                "model": row["model"], "created_at": row["created_at"]}
