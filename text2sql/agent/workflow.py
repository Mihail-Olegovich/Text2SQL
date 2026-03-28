from __future__ import annotations

import operator
from collections.abc import Iterator
from typing import Literal

from langchain.messages import AnyMessage
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from typing_extensions import Annotated, TypedDict


class MessagesState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    llm_calls: int
    system_prompt: str


def build_agent(model_with_tools, tools: list, system_prompt: str, max_llm_calls: int = 20):
    tools_by_name = {tool.name: tool for tool in tools}

    def _safe_writer():
        try:
            return get_stream_writer()
        except Exception:
            return lambda _: None

    def _expected_tool_args(tool) -> list[str]:
        schema = getattr(tool, "args_schema", None)
        if schema is None:
            return []
        model_fields = getattr(schema, "model_fields", None)
        if isinstance(model_fields, dict):
            return sorted(model_fields.keys())
        return []

    def _format_tool_error(tool_name: str, tool_args, exc: Exception, tool=None) -> str:
        expected_args = _expected_tool_args(tool)
        expected_suffix = (
            f"Expected args: {', '.join(expected_args)}."
            if expected_args
            else "Expected args are defined in the tool schema."
        )
        return (
            f"Tool input error for '{tool_name}': {exc}\n"
            f"Received args: {tool_args}\n"
            f"{expected_suffix}"
        )

    def llm_call(state: dict):
        active_system_prompt = state.get("system_prompt", system_prompt)
        writer = _safe_writer()
        writer({"event": "llm_start", "llm_call_index": state.get("llm_calls", 0) + 1})
        combined: AIMessageChunk | None = None
        input_messages = [SystemMessage(content=active_system_prompt)] + state["messages"]
        stream_iter: Iterator[AIMessageChunk] = model_with_tools.stream(input_messages)
        for chunk in stream_iter:
            combined = chunk if combined is None else combined + chunk
            if chunk.content:
                writer({"event": "llm_token", "text": chunk.content})
            reasoning_chunk = chunk.additional_kwargs.get("reasoning")
            if reasoning_chunk:
                writer({"event": "llm_reasoning_token", "text": reasoning_chunk})

        ai_message = AIMessage(
            content=(combined.content if combined is not None else ""),
            additional_kwargs=(combined.additional_kwargs if combined is not None else {}),
            tool_calls=(combined.tool_calls if combined is not None else []),
        )
        writer(
            {
                "event": "llm_complete",
                "has_tool_calls": bool(ai_message.tool_calls),
                "content": ai_message.content,
                "reasoning": ai_message.additional_kwargs.get("reasoning", ""),
            }
        )
        return {
            "messages": [ai_message],
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    def tool_node(state: dict):
        writer = _safe_writer()
        result = []
        for tool_call in state["messages"][-1].tool_calls:
            tool_name = str(tool_call.get("name", "unknown_tool"))
            tool_args = tool_call.get("args", {})
            tool_call_id = str(tool_call.get("id", "unknown_tool_call"))
            writer({"event": "tool_start", "name": tool_name, "args": tool_args})

            tool = tools_by_name.get(tool_name)
            if tool is None:
                observation = (
                    f"Tool input error for '{tool_name}': unknown tool name.\n"
                    f"Available tools: {', '.join(sorted(tools_by_name.keys()))}"
                )
            else:
                try:
                    observation = tool.invoke(tool_args)
                except Exception as exc:
                    observation = _format_tool_error(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        exc=exc,
                        tool=tool,
                    )

            writer(
                {
                    "event": "tool_result",
                    "name": tool_name,
                    "content": observation,
                }
            )
            result.append(ToolMessage(content=str(observation), tool_call_id=tool_call_id))
        return {"messages": result}

    def should_continue(state: MessagesState) -> Literal["tool_node", END]:
        if state.get("llm_calls", 0) >= max_llm_calls:
            return END
        last_message = state["messages"][-1]
        if last_message.tool_calls:
            return "tool_node"
        return END

    agent_builder = StateGraph(MessagesState)
    agent_builder.add_node("llm_call", llm_call)
    agent_builder.add_node("tool_node", tool_node)
    agent_builder.add_edge(START, "llm_call")
    agent_builder.add_conditional_edges("llm_call", should_continue, ["tool_node", END])
    agent_builder.add_edge("tool_node", "llm_call")

    return agent_builder.compile()
