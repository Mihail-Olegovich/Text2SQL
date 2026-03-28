from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from langchain.tools import tool


class DatabaseToolkit:
    def __init__(self, dev_db_path: Path, dev_tables_path: Path) -> None:
        self.dev_db_path = dev_db_path
        self.dev_tables_path = dev_tables_path
        with open(self.dev_tables_path, "r", encoding="utf-8") as file:
            raw = json.load(file)
        self.db_schemas = {entry["db_id"]: entry for entry in raw}

    @staticmethod
    def _read_text_file(path: str) -> str:
        with open(path, "rb") as file:
            payload = file.read()
        for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        return payload.decode("utf-8", errors="replace")

    def _get_pk_fk_info(self, db_name: str, table_name: str) -> str:
        schema = self.db_schemas.get(db_name)
        if not schema:
            return ""

        table_names = schema["table_names_original"]
        try:
            table_idx = next(
                i for i, tbl_name in enumerate(table_names) if tbl_name.lower() == table_name.lower()
            )
        except StopIteration:
            return ""

        columns = schema["column_names_original"]
        col_indices = {i for i, (tbl_idx, _) in enumerate(columns) if tbl_idx == table_idx}

        def _col_label(index: int) -> str:
            tbl_idx, col_name = columns[index]
            return f"{table_names[tbl_idx]}.{col_name}" if tbl_idx >= 0 else col_name

        parts = []

        pk_cols = []
        for pk in schema.get("primary_keys", []):
            indices = pk if isinstance(pk, list) else [pk]
            if any(i in col_indices for i in indices):
                pk_cols.append([columns[i][1] for i in indices])
        if pk_cols:
            parts.append("Primary keys:")
            for group in pk_cols:
                parts.append(f"  - ({', '.join(group)})" if len(group) > 1 else f"  - {group[0]}")

        fk_out = [(fr, to) for fr, to in schema.get("foreign_keys", []) if fr in col_indices]
        fk_in = [(fr, to) for fr, to in schema.get("foreign_keys", []) if to in col_indices]
        if fk_out:
            parts.append("Foreign keys (outgoing):")
            for fr, to in fk_out:
                parts.append(f"  - {columns[fr][1]} -> {_col_label(to)}")
        if fk_in:
            parts.append("Foreign keys (incoming):")
            for fr, to in fk_in:
                parts.append(f"  - {_col_label(fr)} -> {columns[to][1]}")

        return "\n".join(parts)

    def get_db_overview(self, db_name: str) -> str:
        db_dir = os.path.join(self.dev_db_path, db_name)
        sqlite_file = os.path.join(db_dir, f"{db_name}.sqlite")

        conn = sqlite3.connect(sqlite_file)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'"
        )
        tables = sorted(row[0] for row in cursor.fetchall())
        conn.close()

        lines = [f"Database: {db_name}", "", "SQLite tables:"]
        lines.extend(f"  - {table_name}" for table_name in tables)

        doc_path = os.path.join(self.dev_db_path, db_name, "database_description", "documentation.md")
        if os.path.exists(doc_path):
            lines += ["", "Documentation:", self._read_text_file(doc_path)]
        else:
            lines += ["", f"No documentation found for database '{db_name}'."]

        return "\n".join(lines)

    def describe_table(self, db_name: str, table_name: str) -> str:
        db_dir = os.path.join(self.dev_db_path, db_name)
        sqlite_file = os.path.join(db_dir, f"{db_name}.sqlite")
        parts: list[str] = []

        conn = sqlite3.connect(sqlite_file)
        columns = []
        try:
            cursor = conn.execute(f'PRAGMA table_info("{table_name}")')
            columns = cursor.fetchall()
            if columns:
                parts.append(f"SQLite schema for '{table_name}':")
                for col in columns:
                    nullable = "NULL" if col[3] == 0 else "NOT NULL"
                    primary_key = " [PK]" if col[5] else ""
                    parts.append(f"  {col[1]} ({col[2]}, {nullable}{primary_key})")
        finally:
            conn.close()

        desc_dir = os.path.join(db_dir, "database_description")
        csv_path = None
        candidate = os.path.join(desc_dir, f"{table_name}.csv")
        if os.path.exists(candidate):
            csv_path = candidate
        elif os.path.exists(desc_dir):
            for filename in os.listdir(desc_dir):
                if filename.lower() == f"{table_name.lower()}.csv":
                    csv_path = os.path.join(desc_dir, filename)
                    break

        pk_fk = self._get_pk_fk_info(db_name, table_name)
        if pk_fk:
            parts.append(f"\n{pk_fk}")

        if csv_path:
            parts.append(f"\nCSV description ({os.path.basename(csv_path)}):")
            parts.append(self._read_text_file(csv_path))
        elif not columns:
            parts.append(f"No SQLite table or CSV description found for '{table_name}'.")

        return "\n".join(parts)

    def execute_sql(self, db_name: str, sql_query: str) -> str:
        db_dir = os.path.join(self.dev_db_path, db_name)
        sqlite_file = os.path.join(db_dir, f"{db_name}.sqlite")

        conn = sqlite3.connect(sqlite_file)
        try:
            cursor = conn.execute(sql_query)
            if cursor.description is None:
                return "Query returned no results"
            col_names = [desc[0] for desc in cursor.description]
            rows = cursor.fetchmany(51)
            truncated = len(rows) > 50
            rows = rows[:50]

            header = " | ".join(col_names)
            separator = " | ".join("-" * len(col_name) for col_name in col_names)
            lines = [f"Database: {db_name}", "", header, separator]
            for row in rows:
                lines.append(" | ".join(str(value) for value in row))
            if truncated:
                lines.append("\n... (results truncated to 50 rows)")
            return "\n".join(lines)
        except Exception as exc:
            return f"SQL Error: {exc}"
        finally:
            conn.close()

    def as_tools(self) -> list:
        @tool
        def get_db_overview(db_name: str) -> str:
            """List all tables in a database and read its documentation, if available."""

            return self.get_db_overview(db_name)

        @tool
        def describe_table(db_name: str, table_name: str) -> str:
            """Get table schema and CSV description."""

            return self.describe_table(db_name, table_name)

        @tool
        def execute_sql(db_name: str, sql_query: str) -> str:
            """Execute a read-only SQL query against a database."""

            return self.execute_sql(db_name, sql_query)

        return [get_db_overview, describe_table, execute_sql]
