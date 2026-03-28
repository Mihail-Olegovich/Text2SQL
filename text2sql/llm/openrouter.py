from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import requests
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field


class ChatOpenRouter(BaseChatModel):
    """Thin wrapper around OpenRouter that preserves the `reasoning` field."""

    model: str
    api_key: str
    base_url: str = "https://openrouter.ai/api/v1/chat/completions"
    include_reasoning: bool = True
    temperature: float | None = None
    max_tokens: int | None = None
    extra_params: dict = Field(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "openrouter"

    @staticmethod
    def _format_messages(messages: list[BaseMessage]) -> list[dict]:
        result = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                result.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                entry: dict[str, Any] = {"role": "assistant"}
                content = msg.content or ""
                reasoning = msg.additional_kwargs.get("reasoning")
                trace = msg.additional_kwargs.get("trace")
                # Preserve full prior tool outputs + reasoning in the next model context.
                # OpenRouter may ignore custom metadata fields, so we append this trace
                # directly to assistant content to guarantee delivery.
                if reasoning or trace:
                    context_parts: list[str] = []
                    if reasoning:
                        context_parts.append(
                            "\n".join(
                                [
                                    "<previous_reasoning>",
                                    str(reasoning),
                                    "</previous_reasoning>",
                                ]
                            )
                        )
                    if trace:
                        context_parts.append(
                            "\n".join(
                                [
                                    "<previous_tool_trace>",
                                    str(trace),
                                    "</previous_tool_trace>",
                                ]
                            )
                        )
                    trace_block = "\n\n".join(context_parts)
                    content = f"{content}\n\n{trace_block}" if content else trace_block
                entry["content"] = content
                if reasoning:
                    entry["reasoning"] = reasoning
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["args"]),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                result.append(entry)
            elif isinstance(msg, ToolMessage):
                result.append(
                    {
                        "role": "tool",
                        "content": msg.content,
                        "tool_call_id": msg.tool_call_id,
                    }
                )
        return result

    def _build_payload(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._format_messages(messages),
            "include_reasoning": self.include_reasoning,
            **self.extra_params,
        }
        if stop:
            payload["stop"] = stop
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if "tools" in kwargs:
            payload["tools"] = kwargs["tools"]
        return payload

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        payload = self._build_payload(messages=messages, stop=stop, **kwargs)

        resp = requests.post(
            self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        raw_msg = choice["message"]

        additional_kwargs: dict[str, Any] = {}
        if raw_msg.get("reasoning"):
            additional_kwargs["reasoning"] = raw_msg["reasoning"]

        tool_calls = []
        if raw_msg.get("tool_calls"):
            for tc in raw_msg["tool_calls"]:
                args = tc["function"]["arguments"]
                tool_calls.append(
                    {
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "args": json.loads(args) if isinstance(args, str) else args,
                    }
                )

        ai_msg = AIMessage(
            content=raw_msg.get("content") or "",
            additional_kwargs=additional_kwargs,
            tool_calls=tool_calls,
        )
        return ChatResult(generations=[ChatGeneration(message=ai_msg)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        payload = self._build_payload(messages=messages, stop=stop, **kwargs)
        payload["stream"] = True

        with requests.post(
            self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=300,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            # OpenRouter SSE chunks are UTF-8; force decoding to avoid mojibake in non-ASCII output.
            for raw_line in resp.iter_lines(decode_unicode=False):
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data_text = line[len("data:") :].strip()
                if data_text == "[DONE]":
                    break

                try:
                    data = json.loads(data_text)
                except json.JSONDecodeError:
                    continue

                if data.get("error"):
                    raise RuntimeError(data["error"].get("message", "OpenRouter stream error"))

                choices = data.get("choices") or []
                if not choices:
                    continue

                delta = (choices[0] or {}).get("delta") or {}
                content = delta.get("content") or ""
                reasoning = delta.get("reasoning") or ""
                additional_kwargs: dict[str, Any] = {}
                if reasoning:
                    additional_kwargs["reasoning"] = reasoning

                tool_call_chunks = []
                for tc in delta.get("tool_calls") or []:
                    function_data = tc.get("function") or {}
                    tool_call_chunks.append(
                        {
                            "id": tc.get("id"),
                            "name": function_data.get("name"),
                            "args": function_data.get("arguments", ""),
                            "index": tc.get("index"),
                        }
                    )

                if not content and not additional_kwargs and not tool_call_chunks:
                    continue

                message_chunk = AIMessageChunk(
                    content=content,
                    additional_kwargs=additional_kwargs,
                    tool_call_chunks=tool_call_chunks,
                )
                generation_chunk = ChatGenerationChunk(message=message_chunk)
                if run_manager and content:
                    run_manager.on_llm_new_token(content, chunk=generation_chunk)
                if run_manager and reasoning:
                    run_manager.on_llm_new_token(reasoning, chunk=generation_chunk)
                yield generation_chunk

    def bind_tools(self, tools: list, **kwargs: Any):
        formatted = [convert_to_openai_tool(t) for t in tools]
        return self.bind(tools=formatted, **kwargs)


def get_reasoning(msg: AIMessage) -> str | None:
    return msg.additional_kwargs.get("reasoning")


def print_messages(messages: list[BaseMessage]) -> None:
    for msg in messages:
        if isinstance(msg, HumanMessage):
            print(f"{'=' * 32} Human Message {'=' * 33}\n")
            print(msg.content)
        elif isinstance(msg, AIMessage):
            print(f"{'=' * 34} Ai Message {'=' * 34}\n")
            reasoning = get_reasoning(msg)
            if reasoning:
                print(f"[Reasoning]\n{reasoning}\n")
            if msg.tool_calls:
                print("Tool Calls:")
                for tc in msg.tool_calls:
                    print(f"  {tc['name']} ({tc['id']})")
                    print("  Args:")
                    for key, value in tc["args"].items():
                        print(f"    {key}: {value}")
            if msg.content:
                print(msg.content)
        elif isinstance(msg, ToolMessage):
            print(f"{'=' * 33} Tool Message {'=' * 33}\n")
            print(msg.content)
        print()
