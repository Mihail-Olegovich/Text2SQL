from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from text2sql.agent import build_agent
from text2sql.config import ServiceSettings
from text2sql.llm import ChatOpenRouter
from text2sql.prompts import SYSTEM_PROMPT_TEMPLATE, render_system_prompt
from text2sql.tools import DatabaseToolkit, FewShotMemoryToolkit, ProceduralMemoryToolkit
from text2sql.tools.memory_tools import OpenRouterEmbeddingsClient, SQLiteFewShotMemoryStore
from text2sql.tools.memory_tools.procedural_memory import SQLiteProceduralMemoryStore


@dataclass
class AgentResponse:
    messages: list[BaseMessage]

    @property
    def last_ai_message(self) -> AIMessage | None:
        for msg in reversed(self.messages):
            if isinstance(msg, AIMessage):
                return msg
        return None


class Text2SQLService:
    def __init__(self, settings: ServiceSettings | None = None) -> None:
        self.settings = settings or ServiceSettings.defaults()
        self.user_id = self.settings.memory.user_id
        self.store = SQLiteProceduralMemoryStore(self.settings.memory.sqlite_path)
        self.few_shot_store = SQLiteFewShotMemoryStore(self.settings.memory.few_shot_sqlite_path)

        self.model = ChatOpenRouter(
            model=self.settings.model.model_name,
            api_key=self.settings.model.api_key,
            base_url=self.settings.model.base_url,
            include_reasoning=self.settings.model.include_reasoning,
            temperature=self.settings.model.temperature,
            max_tokens=self.settings.model.max_tokens,
        )

        self.db_toolkit = DatabaseToolkit(
            dev_db_path=self.settings.data_paths.dev_db_path,
            dev_tables_path=self.settings.data_paths.dev_tables_path,
        )
        self.memory_toolkit = ProceduralMemoryToolkit(store=self.store, user_id=self.user_id)
        embeddings_client = OpenRouterEmbeddingsClient(
            api_key=self.settings.model.api_key,
            model=self.settings.memory.embedding_model,
            base_url=self.settings.memory.embedding_base_url,
        )
        self.few_shot_memory_toolkit = FewShotMemoryToolkit(
            store=self.few_shot_store,
            user_id=self.user_id,
            embeddings_client=embeddings_client,
        )
        self.tools = (
            self.db_toolkit.as_tools()
            + self.memory_toolkit.as_tools()
            + self.few_shot_memory_toolkit.as_tools()
        )
        self.model_with_tools = self.model.bind_tools(self.tools)
        self.agent = build_agent(
            model_with_tools=self.model_with_tools,
            tools=self.tools,
            system_prompt=SYSTEM_PROMPT_TEMPLATE,
            max_llm_calls=self.settings.agent.max_llm_calls,
        )

    def list_databases(self) -> list[str]:
        return sorted(self.db_toolkit.db_schemas.keys())

    def _format_memories_for_prompt(self, db_name: str) -> str:
        namespace = ("procedural_memory", self.user_id, db_name)
        items = self.store.search(namespace, limit=100)
        if not items:
            return "- No stored procedural memories yet."

        formatted_memories: list[str] = []
        for item in items:
            value = getattr(item, "value", item)
            memory_text = value.get("memory") if isinstance(value, dict) else None
            if memory_text:
                formatted_memories.append(f"- {memory_text}")

        if not formatted_memories:
            return "- No stored procedural memories yet."
        return "\n".join(formatted_memories)

    def ask(
        self,
        db_name: str,
        question: str,
        history: Iterable[BaseMessage] | None = None,
    ) -> AgentResponse:
        request_state = self._build_request_state(
            db_name=db_name,
            question=question,
            history=history,
        )
        result = self.agent.invoke(request_state)
        return AgentResponse(messages=result["messages"])

    def ask_stream(
        self,
        db_name: str,
        question: str,
        history: Iterable[BaseMessage] | None = None,
    ) -> Iterator[dict[str, Any]]:
        request_state = self._build_request_state(
            db_name=db_name,
            question=question,
            history=history,
        )
        for part in self.agent.stream(
            request_state,
            stream_mode=["updates", "messages", "custom"],
            version="v2",
        ):
            yield self._normalize_stream_part(part)

    def _build_request_state(
        self,
        db_name: str,
        question: str,
        history: Iterable[BaseMessage] | None,
    ) -> dict[str, Any]:
        prompt = f"Database name: {db_name}.\nUser question: {question}"
        messages = list(history) if history else []
        messages.append(HumanMessage(content=prompt))
        procedural_memories = self._format_memories_for_prompt(db_name=db_name)
        system_prompt = render_system_prompt(procedural_memories=procedural_memories)
        return {"messages": messages, "system_prompt": system_prompt}

    @staticmethod
    def _normalize_stream_part(part: Any) -> dict[str, Any]:
        if isinstance(part, dict):
            return {
                "type": part.get("type"),
                "ns": part.get("ns", ()),
                "data": part.get("data"),
            }
        if isinstance(part, tuple):
            if len(part) == 2:
                stream_mode, data = part
                return {"type": stream_mode, "ns": (), "data": data}
            if len(part) == 3:
                namespace, stream_mode, data = part
                return {"type": stream_mode, "ns": namespace, "data": data}
        return {"type": "unknown", "ns": (), "data": part}
