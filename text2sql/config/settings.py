from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DataPaths:
    repo_root: Path
    train_db_path: Path
    dev_db_path: Path
    train_tables_path: Path
    dev_tables_path: Path
    dev_json_path: Path

    @classmethod
    def defaults(cls) -> "DataPaths":
        root = _repo_root()
        return cls(
            repo_root=root,
            train_db_path=root / "data" / "train" / "train_databases",
            dev_db_path=root / "data" / "dev_20240627" / "dev_databases",
            train_tables_path=root / "data" / "train" / "train_tables.json",
            dev_tables_path=root / "data" / "dev_20240627" / "dev_tables.json",
            dev_json_path=root / "data" / "dev_20240627" / "dev.json",
        )


@dataclass(frozen=True)
class ModelSettings:
    api_key: str
    model_name: str = "qwen/qwen3.5-397b-a17b"
    base_url: str = "https://openrouter.ai/api/v1/chat/completions"
    include_reasoning: bool = True
    temperature: float | None = None
    max_tokens: int | None = None

    @classmethod
    def from_env(cls) -> "ModelSettings":
        load_dotenv()
        api_key = os.getenv("OPEN_ROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPEN_ROUTER_API_KEY is not set in the environment.")
        return cls(api_key=api_key)


@dataclass(frozen=True)
class AgentSettings:
    max_llm_calls: int = 20


@dataclass(frozen=True)
class MemorySettings:
    sqlite_path: Path
    few_shot_sqlite_path: Path
    embedding_model: str = "qwen/qwen3-embedding-8b"
    embedding_base_url: str = "https://openrouter.ai/api/v1/embeddings"
    user_id: str = "default_user"

    @classmethod
    def defaults(cls) -> "MemorySettings":
        root = _repo_root()
        return cls(
            sqlite_path=root / ".text2sql" / "procedural_memory.sqlite3",
            few_shot_sqlite_path=root / ".text2sql" / "few_shot_memory.sqlite3",
        )


@dataclass(frozen=True)
class ServiceSettings:
    data_paths: DataPaths
    model: ModelSettings
    agent: AgentSettings
    memory: MemorySettings

    @classmethod
    def defaults(cls) -> "ServiceSettings":
        return cls(
            data_paths=DataPaths.defaults(),
            model=ModelSettings.from_env(),
            agent=AgentSettings(),
            memory=MemorySettings.defaults(),
        )
