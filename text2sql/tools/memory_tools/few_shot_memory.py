from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

import requests
from langchain.tools import tool


class OpenRouterEmbeddingsClient:
    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def embed(self, text: str) -> list[float]:
        response = requests.post(
            self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"model": self.model, "input": text},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or []
        if not data or "embedding" not in data[0]:
            raise ValueError("Embeddings response does not contain vectors.")
        vector = data[0]["embedding"]
        if not isinstance(vector, list) or not vector:
            raise ValueError("Embeddings response contains an invalid vector.")
        return [float(value) for value in vector]


class SQLiteFewShotMemoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS few_shot_memory (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    user_query TEXT NOT NULL,
                    sql_query TEXT NOT NULL,
                    note TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, key)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_few_shot_memory_namespace_created_at "
                "ON few_shot_memory(namespace, created_at)"
            )
            conn.commit()

    @staticmethod
    def _namespace_to_text(namespace: tuple[str, ...]) -> str:
        return "::".join(namespace)

    def put(
        self,
        namespace: tuple[str, ...],
        key: str,
        user_query: str,
        sql_query: str,
        note: str,
        embedding: list[float],
    ) -> None:
        namespace_text = self._namespace_to_text(namespace)
        created_at = datetime.now(timezone.utc).isoformat()
        embedding_json = json.dumps(embedding, ensure_ascii=True)
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO few_shot_memory(
                        namespace, key, user_query, sql_query, note, embedding_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(namespace, key)
                    DO UPDATE SET
                        user_query = excluded.user_query,
                        sql_query = excluded.sql_query,
                        note = excluded.note,
                        embedding_json = excluded.embedding_json,
                        created_at = excluded.created_at
                    """,
                    (
                        namespace_text,
                        key,
                        user_query,
                        sql_query,
                        note,
                        embedding_json,
                        created_at,
                    ),
                )
                conn.commit()

    def search(
        self,
        namespace: tuple[str, ...],
        query_embedding: list[float],
        limit: int,
    ) -> list[dict]:
        namespace_text = self._namespace_to_text(namespace)
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT key, user_query, sql_query, note, embedding_json, created_at
                    FROM few_shot_memory
                    WHERE namespace = ?
                    """,
                    (namespace_text,),
                ).fetchall()

        scored: list[dict] = []
        for row in rows:
            key, user_query, sql_query, note, embedding_json, created_at = row
            stored_embedding = json.loads(embedding_json)
            score = self._cosine_similarity(query_embedding, stored_embedding)
            scored.append(
                {
                    "key": key,
                    "user_query": user_query,
                    "sql_query": sql_query,
                    "note": note,
                    "created_at": created_at,
                    "score": score,
                }
            )

        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:limit]

    @staticmethod
    def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        if len(vec_a) != len(vec_b) or not vec_a:
            return -1.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0.0 or norm_b == 0.0:
            return -1.0
        return dot / (norm_a * norm_b)


class FewShotMemoryToolkit:
    def __init__(
        self,
        store: SQLiteFewShotMemoryStore,
        user_id: str,
        embeddings_client: OpenRouterEmbeddingsClient,
    ) -> None:
        self.store = store
        self.user_id = user_id
        self.embeddings_client = embeddings_client

    def add_sql_example(
        self,
        db_name: str,
        user_query: str,
        sql_query: str,
        note: str = "",
    ) -> str:
        cleaned_user_query = user_query.strip()
        cleaned_sql_query = sql_query.strip()
        cleaned_note = note.strip()

        if not cleaned_user_query:
            return "Example was not saved: user_query is empty."
        if not cleaned_sql_query:
            return "Example was not saved: sql_query is empty."

        namespace = ("few_shot_memory", self.user_id, db_name)
        key = str(uuid4())
        embedding_input = (
            f"User question: {cleaned_user_query}\n"
            f"SQL pattern:\n{cleaned_sql_query}\n"
            f"Note: {cleaned_note}"
        )
        try:
            embedding = self.embeddings_client.embed(embedding_input)
        except Exception as exc:
            return f"Example was not saved: embedding request failed ({exc})."

        self.store.put(
            namespace=namespace,
            key=key,
            user_query=cleaned_user_query,
            sql_query=cleaned_sql_query,
            note=cleaned_note,
            embedding=embedding,
        )
        return f"Few-shot example saved for db '{db_name}'."

    def search_sql_examples(self, db_name: str, user_query: str, limit: int = 3) -> str:
        cleaned_query = user_query.strip()
        if not cleaned_query:
            return "Search query is empty."

        safe_limit = max(1, min(limit, 10))
        namespace = ("few_shot_memory", self.user_id, db_name)
        try:
            query_embedding = self.embeddings_client.embed(f"User question: {cleaned_query}")
        except Exception as exc:
            return f"Could not search few-shot examples: embedding request failed ({exc})."

        matches = self.store.search(
            namespace=namespace,
            query_embedding=query_embedding,
            limit=safe_limit,
        )
        if not matches:
            return "No few-shot SQL examples found for this database."

        lines: list[str] = [f"Top {len(matches)} few-shot SQL examples for '{db_name}':"]
        for idx, item in enumerate(matches, start=1):
            score = item.get("score", -1.0)
            lines.append(f"\n[{idx}] similarity={score:.3f}")
            lines.append(f"Question: {item['user_query']}")
            if item["note"]:
                lines.append(f"Note: {item['note']}")
            lines.append("SQL:")
            lines.append(item["sql_query"])
        return "\n".join(lines)

    def as_tools(self) -> list:
        @tool
        def add_sql_example(db_name: str, user_query: str, sql_query: str, note: str = "") -> str:
            """Save a reusable few-shot example pair (user question -> SQL) for this user and database."""

            return self.add_sql_example(
                db_name=db_name,
                user_query=user_query,
                sql_query=sql_query,
                note=note,
            )

        @tool
        def search_sql_examples(db_name: str, user_query: str, limit: int = 3) -> str:
            """Semantic search over stored few-shot SQL examples for this user and database."""

            return self.search_sql_examples(
                db_name=db_name,
                user_query=user_query,
                limit=limit,
            )

        return [add_sql_example, search_sql_examples]
