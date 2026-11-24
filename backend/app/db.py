"""SQLite storage for sessions and their messages. The schema is created on startup."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id    TEXT PRIMARY KEY,
  system_prompt TEXT,              -- this session's override; NULL means the default prompt
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT NOT NULL,
  role        TEXT NOT NULL,       -- 'user' or 'assistant'
  content     TEXT NOT NULL,
  metadata    TEXT,                -- JSON string, assistant messages only
  created_at  TEXT NOT NULL        -- server time, ISO-8601
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);
"""


class Database:
    def __init__(self, url: str) -> None:
        if not url.startswith("sqlite:///"):
            raise ValueError("DATABASE_URL must be a SQLite URL such as sqlite:////data/app.db")
        self.path = url.removeprefix("sqlite:///")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # A short-lived connection per call keeps every request thread-safe.
        return sqlite3.connect(self.path, timeout=10)

    def ping(self) -> None:
        """Raise if the database cannot be read."""
        with closing(self._connect()) as conn:
            conn.execute("SELECT 1 FROM messages LIMIT 1")

    def get_session(self, session_id: str) -> dict | None:
        """The session's system prompt and its messages, oldest first; None for an unknown session."""
        with closing(self._connect()) as conn:
            session = conn.execute(
                "SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if session is None:
                return None
            rows = conn.execute(
                "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
        messages = []
        for role, content, metadata in rows:
            message = {"role": role, "content": content}
            if metadata:
                message["metadata"] = json.loads(metadata)
            messages.append(message)
        return {"system_prompt": session[0], "messages": messages}

    def save_exchange(
        self, session_id: str, system_prompt: str | None, user_message: str, reply: str, metadata: dict
    ) -> None:
        """Store one user message and its reply. One transaction, so a turn is saved whole or not at all."""
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO sessions (session_id, system_prompt, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET system_prompt = excluded.system_prompt",
                (session_id, system_prompt, now),
            )
            conn.executemany(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                [
                    (session_id, "user", user_message, None, now),
                    (session_id, "assistant", reply, json.dumps(metadata), now),
                ],
            )
