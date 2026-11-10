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
        return {"conversation_id": conversation["conversation_id"], "title": conversation["title"],
                "created_at": conversation["created_at"], "updated_at": conversation["updated_at"],
                "messages": [self._message(row) for row in rows]}

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
            connection.execute(insert, ("amsg_" + uuid.uuid4().hex[:16], conversation_id, seq + 2, "assistant",
                                        answer["answer"], json.dumps(answer.get("sources") or [], ensure_ascii=False),
                                        json.dumps(answer.get("suggestions") or [], ensure_ascii=False, default=str),
                                        answer.get("model"), now))
            connection.commit()
        return {"conversation_id": conversation_id, "updated_at": now}

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
