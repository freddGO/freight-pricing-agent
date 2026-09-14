"""Persistent SessionService/MemoryService backends (SQLite, $0 to run).

`InMemorySessionService`/`InMemoryMemoryService` (used by default in
`cli_client.py`) live only in the Python process's RAM — see
`client/README.md`'s "where does the data live" deep dive. This module
provides the durable counterpart: sessions persisted via ADK's built-in
`DatabaseSessionService`, and a hand-rolled `SqliteMemoryService` that
mirrors ADK's own `InMemoryMemoryService` keyword-overlap retrieval
algorithm exactly, but backs it with a SQLite file instead of a dict —
so memory survives past the lifetime of any single process, for the cost
of a local file on disk.

No new paid service, no GCP project, no billing account: everything here
runs against a `.data/*.db` file created next to the repo. See
`client/README.md` for the cost/complexity spectrum this sits on (SQLite
free tier, all the way up to Vertex AI Memory Bank's managed + LLM-
extracted memory).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import aiosqlite
from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.sessions import BaseSessionService, DatabaseSessionService, InMemorySessionService
from google.adk.sessions.session import Session
from google.genai import types

DATA_DIR = Path(__file__).resolve().parent.parent / ".data"

_MAX_SEARCH_RESULTS = 10  # matches InMemoryMemoryService's cap, for an apples-to-apples swap


def _extract_words_lower(text: str) -> set[str]:
    """Same tokenization ADK's InMemoryMemoryService uses: lowercase \\w+ tokens."""
    return set(word.lower() for word in re.findall(r"\w+", text))


def _format_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).isoformat()


class SqliteMemoryService(BaseMemoryService):
    """Durable, keyword-search memory service backed by a local SQLite file.

    Deliberately mirrors `google.adk.memory.in_memory_memory_service.InMemoryMemoryService`'s
    algorithm (same tokenizer, same "count shared words, keep top 10" scoring)
    so the only thing that changes versus the in-memory version is where the
    data lives — not how relevant a memory is judged to be. That keeps the
    swap an honest apples-to-apples persistence upgrade, not a quality change
    smuggled in alongside it.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

    async def _ensure_schema(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_events (
                app_name  TEXT NOT NULL,
                user_id   TEXT NOT NULL,
                session_id TEXT NOT NULL,
                event_id  TEXT NOT NULL,
                author    TEXT,
                role      TEXT,
                timestamp TEXT,
                text      TEXT NOT NULL,
                PRIMARY KEY (app_name, user_id, session_id, event_id)
            )
            """
        )
        await db.commit()

    async def add_session_to_memory(self, session: Session) -> None:
        rows = []
        for event in session.events:
            if not event.content or not event.content.parts:
                continue
            text = " ".join(part.text for part in event.content.parts if part.text)
            if not text:
                continue
            rows.append((
                session.app_name,
                session.user_id,
                session.id,
                event.id,
                event.author,
                event.content.role,
                _format_timestamp(event.timestamp),
                text,
            ))
        if not rows:
            return

        async with aiosqlite.connect(self._db_path) as db:
            await self._ensure_schema(db)
            await db.executemany(
                "INSERT OR REPLACE INTO memory_events "
                "(app_name, user_id, session_id, event_id, author, role, timestamp, text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            await db.commit()

    async def search_memory(self, *, app_name: str, user_id: str, query: str) -> SearchMemoryResponse:
        async with aiosqlite.connect(self._db_path) as db:
            await self._ensure_schema(db)
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT author, role, timestamp, text FROM memory_events "
                "WHERE app_name = ? AND user_id = ?",
                (app_name, user_id),
            )
            rows = await cursor.fetchall()

        words_in_query = _extract_words_lower(query)
        scored: list[tuple[int, MemoryEntry]] = []
        for row in rows:
            words_in_event = _extract_words_lower(row["text"])
            if not words_in_event:
                continue
            matched = sum(1 for w in words_in_query if w in words_in_event)
            if matched:
                scored.append((
                    matched,
                    MemoryEntry(
                        content=types.Content(
                            role=row["role"] or "model",
                            parts=[types.Part(text=row["text"])],
                        ),
                        author=row["author"],
                        timestamp=row["timestamp"],
                    ),
                ))

        scored.sort(key=lambda scored_memory: -scored_memory[0])
        return SearchMemoryResponse(memories=[memory for _, memory in scored[:_MAX_SEARCH_RESULTS]])


def build_services(persistent: bool) -> tuple[BaseSessionService, BaseMemoryService]:
    """Returns (session_service, memory_service) for the requested durability mode.

    `persistent=False` (default): process-RAM only, reset on every run — good
    for a quick demo. `persistent=True`: SQLite files under `.data/`, so a
    session/memory started in one process run is still there the next time
    you run the client.
    """
    if not persistent:
        return InMemorySessionService(), InMemoryMemoryService()

    DATA_DIR.mkdir(exist_ok=True)
    session_service = DatabaseSessionService(
        db_url=f"sqlite+aiosqlite:///{DATA_DIR / 'sessions.db'}"
    )
    memory_service = SqliteMemoryService(DATA_DIR / "memory.db")
    return session_service, memory_service
