from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from langchain.tools import tool


class SQLiteProceduralMemoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS procedural_memory (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, key)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_procedural_memory_namespace_created_at "
                "ON procedural_memory(namespace, created_at)"
            )
            conn.commit()

    @staticmethod
    def _namespace_to_text(namespace: tuple[str, ...]) -> str:
        return "::".join(namespace)

    def put(self, namespace: tuple[str, ...], key: str, value: dict) -> None:
        namespace_text = self._namespace_to_text(namespace)
        created_at = value.get("created_at") or datetime.now(timezone.utc).isoformat()
        value_json = json.dumps(value, ensure_ascii=True)
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO procedural_memory(namespace, key, value_json, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(namespace, key)
                    DO UPDATE SET value_json = excluded.value_json, created_at = excluded.created_at
                    """,
                    (namespace_text, key, value_json, created_at),
                )
                conn.commit()

    def search(self, namespace: tuple[str, ...], limit: int = 100) -> list[dict]:
        namespace_text = self._namespace_to_text(namespace)
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT value_json
                    FROM procedural_memory
                    WHERE namespace = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (namespace_text, limit),
                ).fetchall()
        return [json.loads(row[0]) for row in rows]


class ProceduralMemoryToolkit:
    def __init__(self, store, user_id: str) -> None:
        self.store = store
        self.user_id = user_id

    def add_procedural_memory(self, db_name: str, memory: str) -> str:
        cleaned_memory = memory.strip()
        if not cleaned_memory:
            return "Memory was not saved: memory is empty."

        namespace = ("procedural_memory", self.user_id, db_name)
        memory_key = str(uuid4())
        self.store.put(
            namespace=namespace,
            key=memory_key,
            value={
                "memory": cleaned_memory,
                "db_name": db_name,
                "user_id": self.user_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return f"Memory saved for db '{db_name}'."

    def as_tools(self) -> list:
        @tool
        def add_procedural_memory(db_name: str, memory: str) -> str:
            """Store a short procedural hint for the current user and database."""

            return self.add_procedural_memory(db_name, memory)

        return [add_procedural_memory]
