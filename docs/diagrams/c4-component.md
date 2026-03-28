# C4 Component — Agent Core

Внутреннее устройство ядра системы: `Text2SQLService`, `LangGraph Agent` и `Tool Layer`.

```mermaid
C4Component
    title Text2SQL Agent — Core Components

    Container_Boundary(service_b, "Text2SQLService (service.py)") {
        Component(state_builder, "_build_request_state", "Python method", "Собирает MessagesState:\nhistory + HumanMessage(db_name + question)\n+ render_system_prompt(memories)")
        Component(prompt_renderer, "render_system_prompt", "Python function", "Вставляет процедурные правила\nв SYSTEM_PROMPT_TEMPLATE\n→ системный промпт строкой")
        Component(mem_loader, "_format_memories_for_prompt", "Python method", "ProceduralMemoryStore.search\n(procedural_memory, user_id, db_name)\n→ bullet-list строка")
        Component(normalizer, "_normalize_stream_part", "Python method", "Нормализует LangGraph stream parts\n→ {type, ns, data}")
    }

    Container_Boundary(agent_b, "LangGraph StateGraph (workflow.py)") {
        Component(msg_state, "MessagesState", "TypedDict", "messages: Annotated[list, operator.add]\nllm_calls: int\nsystem_prompt: str")
        Component(llm_node, "llm_call", "Graph node", "Добавляет SystemMessage(system_prompt)\nвызывает model_with_tools.stream()\nиспускает: llm_start / llm_token /\nllm_reasoning_token / llm_complete\nincrements llm_calls")
        Component(tool_exec, "tool_node", "Graph node", "Для каждого tool_call:\n1. tool_start event\n2. tool.invoke(args) или error\n3. tool_result event\n→ список ToolMessage")
        Component(router, "should_continue", "Conditional edge", "llm_calls >= 20 → END\nlast_msg.tool_calls → tool_node\nиначе → END")
    }

    Container_Boundary(tools_b, "Tool Layer") {
        Component(db_tk, "DatabaseToolkit", "Python class", "get_db_overview: tables + docs\ndescribe_table: PRAGMA + CSV\nexecute_sql: SELECT, max 50 rows")
        Component(proc_tk, "ProceduralMemoryToolkit", "Python class", "add_procedural_memory:\nupsert в SQLiteProceduralMemoryStore\nNamespace: procedural_memory::user::db")
        Component(fs_tk, "FewShotMemoryToolkit", "Python class", "search_sql_examples: embed → cosine → top-k\nadd_sql_example: embed → upsert\nNamespace: few_shot_memory::user::db")
        Component(embed_cli, "OpenRouterEmbeddingsClient", "Python / HTTP", "embed(text) → list[float]\nPOST /embeddings, timeout=60s\nmodel: qwen/qwen3-embedding-8b")
    }

    Rel(state_builder, mem_loader, "вызывает")
    Rel(state_builder, prompt_renderer, "передаёт memories строкой")
    Rel(state_builder, msg_state, "инициализирует начальное состояние")
    Rel(llm_node, router, "→ переход после генерации")
    Rel(router, tool_exec, "если tool_calls и llm_calls < 20")
    Rel(tool_exec, db_tk, "вызовы DB-инструментов")
    Rel(tool_exec, proc_tk, "вызов add_procedural_memory")
    Rel(tool_exec, fs_tk, "вызовы memory-инструментов")
    Rel(fs_tk, embed_cli, "embed(query) и embed(example)")
    Rel(normalizer, llm_node, "читает custom events из stream")
```

## Жизненный цикл компонентов

| Компонент | Время жизни | Инициализация |
|-----------|-------------|---------------|
| `Text2SQLService` | Весь процесс | При запуске скрипта; повторная не нужна |
| `LangGraph StateGraph` | Компилируется один раз | `build_agent()` при старте `Text2SQLService` |
| `MessagesState` | Один запрос к агенту | `_build_request_state()` перед каждым `ask` / `ask_stream` |
| `SQLiteProceduralMemoryStore` | Весь процесс | При старте; персистентен между запросами |
| `SQLiteFewShotMemoryStore` | Весь процесс | При старте; персистентен между запросами |
| `OpenRouterEmbeddingsClient` | Весь процесс | При старте `Text2SQLService`; stateless |
