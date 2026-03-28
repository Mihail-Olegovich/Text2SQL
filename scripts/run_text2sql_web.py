from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import gradio as gr
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from text2sql.service import Text2SQLService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Text2SQL web interface.")
    parser.add_argument("--host", default="127.0.0.1", help="Host for Gradio server.")
    parser.add_argument("--port", type=int, default=7860, help="Port for Gradio server.")
    parser.add_argument("--share", action="store_true", help="Enable Gradio public share link.")
    return parser.parse_args()


def _ensure_localhost_bypasses_proxy() -> None:
    localhost_hosts = {"127.0.0.1", "localhost", "::1"}
    for key in ("NO_PROXY", "no_proxy"):
        current = os.environ.get(key, "")
        values = {value.strip() for value in current.split(",") if value.strip()}
        merged = values | localhost_hosts
        os.environ[key] = ",".join(sorted(merged))


def _format_tool_args(args: Any) -> str:
    if isinstance(args, dict) and args:
        lines = ["| arg | value |", "| --- | --- |"]
        for key, value in args.items():
            safe_key = str(key).replace("|", "\\|")
            safe_value = str(value).replace("|", "\\|").replace("\n", "<br>")
            lines.append(f"| `{safe_key}` | {safe_value} |")
        return "\n".join(lines)
    return f"```text\n{args if args is not None else '{}'}\n```"


def _tool_message_content(args: Any, output: str = "", pending: bool = False) -> str:
    output_text = output.strip() if output.strip() else "_Waiting for tool output..._"
    header = ["**Args**", _format_tool_args(args), "", "**Output**", "```text", output_text, "```"]
    if pending:
        header.append("\n_In progress..._")
    return "\n".join(header)


def _make_chat_message(
    role: str,
    content: str,
    *,
    title: str | None = None,
    status: str | None = None,
    msg_id: str | None = None,
    parent_id: str | None = None,
    log: str | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": role, "content": content}
    metadata: dict[str, Any] = {}
    if title is not None:
        metadata["title"] = title
    if status is not None:
        metadata["status"] = status
    if msg_id is not None:
        metadata["id"] = msg_id
    if parent_id is not None:
        metadata["parent_id"] = parent_id
    if log:
        metadata["log"] = log
    if metadata:
        message["metadata"] = metadata
    return message


def _append_trace_message(
    chat_history: list[dict[str, Any]],
    final_answer_index: int,
    message: dict[str, Any],
) -> tuple[int, int]:
    chat_history.insert(final_answer_index, message)
    inserted_index = final_answer_index
    return inserted_index, final_answer_index + 1


def _format_trace_for_history(
    reasoning_sections: list[str],
    tool_events: list[dict[str, Any]],
) -> str:
    payload = {
        "reasoning_steps": reasoning_sections,
        "tool_calls": tool_events,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_demo(service: Text2SQLService) -> gr.Blocks:
    db_names = service.list_databases()

    def run_query(
        db_name: str,
        question: str,
        chat_history: list[dict] | None,
        lc_history: list[BaseMessage] | None,
    ):
        chat_history = list(chat_history or [])
        lc_history = list(lc_history or [])

        if not db_name:
            chat_history.append({"role": "assistant", "content": "Choose a database."})
            yield chat_history, lc_history, ""
            return
        if not question or not question.strip():
            chat_history.append({"role": "assistant", "content": "Enter a question."})
            yield chat_history, lc_history, ""
            return

        display_user_message = question.strip()
        chat_history.append(_make_chat_message(role="user", content=display_user_message))
        chat_history.append(_make_chat_message(role="assistant", content="_Thinking..._"))
        final_answer_index = len(chat_history) - 1
        yield chat_history, lc_history, ""

        answer_text = ""
        current_llm_output = ""
        llm_call_index = 0
        reasoning_sections: list[str] = []
        tool_events: list[dict[str, Any]] = []
        active_reasoning_index: int | None = None
        active_reasoning_id: str | None = None
        active_tool_index: int | None = None
        active_tool_name = ""
        active_tool_args: Any = {}
        active_reasoning_started_at: float | None = None
        active_tool_started_at: float | None = None

        try:
            for part in service.ask_stream(
                db_name=db_name.strip(),
                question=question.strip(),
                history=lc_history,
            ):
                part_type = str(part.get("type"))
                if part_type == "custom":
                    data = part.get("data", {})
                    if not isinstance(data, dict):
                        data = {}
                    event = data.get("event")
                    if event == "llm_start":
                        llm_call_index += 1
                        current_llm_output = ""
                        reasoning_id = f"llm-{llm_call_index}"
                        reasoning_message = _make_chat_message(
                            role="assistant",
                            content="",
                            title=f"Thinking #{llm_call_index}",
                            status="pending",
                            msg_id=reasoning_id,
                        )
                        active_reasoning_index, final_answer_index = _append_trace_message(
                            chat_history=chat_history,
                            final_answer_index=final_answer_index,
                            message=reasoning_message,
                        )
                        active_reasoning_id = reasoning_id
                        active_reasoning_started_at = time.monotonic()
                    elif event == "llm_token":
                        token = data.get("text", "")
                        current_llm_output += token
                    elif event == "llm_reasoning_token":
                        token = str(data.get("text", ""))
                        if active_reasoning_index is not None and token:
                            chat_history[active_reasoning_index]["content"] += token
                    elif event == "llm_complete":
                        content = data.get("content", current_llm_output)
                        reasoning = str(data.get("reasoning", "")).strip()
                        has_tool_calls = bool(data.get("has_tool_calls"))
                        if active_reasoning_index is not None:
                            metadata = chat_history[active_reasoning_index].setdefault("metadata", {})
                            if active_reasoning_started_at is not None:
                                metadata["log"] = f"{time.monotonic() - active_reasoning_started_at:.1f}s"
                            metadata["status"] = "done"
                            if not chat_history[active_reasoning_index]["content"].strip():
                                chat_history[active_reasoning_index]["content"] = (
                                    reasoning if reasoning else "_No explicit reasoning was streamed._"
                                )
                        if reasoning:
                            reasoning_sections.append(reasoning)
                        if active_reasoning_index is not None:
                            active_reasoning_index = None
                            active_reasoning_started_at = None
                        if not has_tool_calls:
                            answer_text = content
                    elif event == "tool_start":
                        active_tool_name = str(data.get("name", "unknown_tool"))
                        active_tool_args = data.get("args", {})
                        tool_message = _make_chat_message(
                            role="assistant",
                            content=_tool_message_content(active_tool_args, pending=True),
                            title=f"Using tool `{active_tool_name}`",
                            status="pending",
                            parent_id=active_reasoning_id,
                        )
                        active_tool_index, final_answer_index = _append_trace_message(
                            chat_history=chat_history,
                            final_answer_index=final_answer_index,
                            message=tool_message,
                        )
                        active_tool_started_at = time.monotonic()
                    elif event == "tool_result":
                        tool_name = str(data.get("name", active_tool_name or "unknown_tool"))
                        result_text = str(data.get("content", ""))
                        if active_tool_index is not None:
                            chat_history[active_tool_index]["content"] = _tool_message_content(
                                args=active_tool_args,
                                output=result_text,
                                pending=False,
                            )
                            metadata = chat_history[active_tool_index].setdefault("metadata", {})
                            if active_tool_started_at is not None:
                                metadata["log"] = f"{time.monotonic() - active_tool_started_at:.1f}s"
                            metadata["status"] = "done"
                        tool_events.append(
                            {
                                "name": tool_name,
                                "args": active_tool_args,
                                "output": result_text,
                            }
                        )
                        active_tool_index = None
                        active_tool_name = ""
                        active_tool_args = {}
                        active_tool_started_at = None

                elif part_type == "updates":
                    update_data = part.get("data", {})
                    if isinstance(update_data, dict):
                        for node_name, node_update in update_data.items():
                            if node_name == "llm_call" and isinstance(node_update, dict):
                                messages = node_update.get("messages", [])
                                if messages:
                                    last_message = messages[-1]
                                    tool_calls = getattr(last_message, "tool_calls", []) or []
                                    if not tool_calls:
                                        answer_text = getattr(last_message, "content", answer_text)

                live_answer = answer_text or current_llm_output
                chat_history[final_answer_index]["content"] = live_answer.strip() if live_answer.strip() else "_Thinking..._"
                yield chat_history, lc_history, ""

            if active_reasoning_index is not None:
                metadata = chat_history[active_reasoning_index].setdefault("metadata", {})
                metadata["status"] = "done"
                chat_history[active_reasoning_index]["content"] += (
                    "\n\n_Streaming interrupted before this reasoning block completed._"
                )
                active_reasoning_index = None
            if active_tool_index is not None:
                metadata = chat_history[active_tool_index].setdefault("metadata", {})
                metadata["status"] = "done"
                chat_history[active_tool_index]["content"] += (
                    "\n\n_Tool call started but no tool result was received in stream._"
                )
                active_tool_index = None

            final_answer = (answer_text or current_llm_output).strip() or "Request finished with no textual answer."
            final_reasoning = "\n\n".join(s for s in reasoning_sections if s.strip()).strip()
            final_trace = _format_trace_for_history(reasoning_sections=reasoning_sections, tool_events=tool_events)
            if not (answer_text or current_llm_output).strip():
                if final_reasoning:
                    final_answer = (
                        "Model did not return a final answer message. "
                        "Using the latest reasoning block as fallback:\n\n"
                        f"{reasoning_sections[-1]}"
                    )
                elif tool_events:
                    final_answer = (
                        "Model stopped after tool execution without a final answer. "
                        "Please retry the same question."
                    )
            lc_history.append(
                HumanMessage(
                    content=f"Database name: {db_name.strip()}.\nUser question: {question.strip()}"
                )
            )
            lc_history.append(
                AIMessage(
                    content=final_answer,
                    additional_kwargs={
                        "reasoning": final_reasoning,
                        "trace": final_trace,
                    },
                )
            )
            chat_history[final_answer_index]["content"] = final_answer
            yield chat_history, lc_history, ""
        except Exception as exc:
            if active_reasoning_index is not None:
                metadata = chat_history[active_reasoning_index].setdefault("metadata", {})
                metadata["status"] = "done"
                chat_history[active_reasoning_index]["content"] += (
                    f"\n\nError while generating reasoning: {exc}"
                )
            if active_tool_index is not None:
                metadata = chat_history[active_tool_index].setdefault("metadata", {})
                metadata["status"] = "done"
                chat_history[active_tool_index]["content"] += f"\n\nTool failed: {exc}"
            chat_history[final_answer_index]["content"] = answer_text or "Request failed."
            yield chat_history, lc_history, ""

    def clear_chat():
        return [], [], ""

    with gr.Blocks(title="Text2SQL Agent") as demo:
        gr.Markdown("## Text2SQL Chat")
        with gr.Row():
            db_name = gr.Dropdown(
                choices=db_names,
                value=db_names[0] if db_names else None,
                label="Database",
                allow_custom_value=True,
            )
        chatbot = gr.Chatbot(label="Dialog", type="messages", height=560, allow_tags=False)
        lc_history = gr.State([])
        with gr.Row():
            question = gr.Textbox(
                label="Message",
                lines=3,
                placeholder="Ask a question in natural language",
                scale=8,
            )
            submit = gr.Button("Send", scale=1)
            clear = gr.Button("Clear chat", scale=1)
        submit.click(
            run_query,
            inputs=[db_name, question, chatbot, lc_history],
            outputs=[chatbot, lc_history, question],
        )
        question.submit(
            run_query,
            inputs=[db_name, question, chatbot, lc_history],
            outputs=[chatbot, lc_history, question],
        )
        clear.click(
            clear_chat,
            outputs=[chatbot, lc_history, question],
        )

    return demo


def main() -> None:
    args = parse_args()
    _ensure_localhost_bypasses_proxy()
    service = Text2SQLService()
    demo = build_demo(service)
    demo.launch(server_name=args.host, server_port=args.port, share=args.share)


if __name__ == "__main__":
    main()
