# C4 Container — Text2SQL Agent

Внутренние контейнеры приложения и их взаимодействие.

```mermaid
C4Container
    title Text2SQL Agent — Containers

    Person(user, "Пользователь")
    Person(dev, "Разработчик")
    System_Ext(openrouter, "OpenRouter API")

    Container_Boundary(app, "Text2SQL Application") {
        Container(gradio, "Gradio Web UI", "Python / Gradio 5", "Веб-интерфейс: выбор БД,\nчат с потоковым выводом,\nreasoning- и tool-блоки")
        Container(cli, "CLI Runner", "Python", "scripts/run_text2sql_service.py\nСинхронный запуск одного запроса")
        Container(service, "Text2SQLService", "Python", "Фасад: сборка MessagesState,\nзапуск LangGraph-графа,\nнормализация событий")
        Container(agent, "LangGraph Agent", "LangGraph >= 1.0", "StateGraph:\nllm_call ↔ tool_node цикл\nstop: no tool_calls OR max_llm_calls")
        Container(llm_client, "ChatOpenRouter", "Python / HTTPS", "BaseChatModel:\nSSE-стриминг, reasoning-инъекция,\nbind_tools → OpenAI tool format")
        Container(db_tools, "DatabaseToolkit", "Python / SQLite", "3 инструмента:\nget_db_overview, describe_table,\nexecute_sql (read-only)")
        Container(mem_tools, "MemoryToolkits", "Python / SQLite", "ProceduralMemoryToolkit (1 tool)\nFewShotMemoryToolkit (2 tools)\n+ OpenRouterEmbeddingsClient")
    }

    ContainerDb(bird_db, "BIRD SQLite Databases", "SQLite (read-only)", "data/dev_20240627/dev_databases/\n95 баз данных + database_description/")
    ContainerDb(proc_db, "Procedural Memory", "SQLite", ".text2sql/procedural_memory.sqlite3")
    ContainerDb(few_db, "Few-shot Memory", "SQLite", ".text2sql/few_shot_memory.sqlite3")

    Rel(user, gradio, "HTTPS / Gradio WebSocket")
    Rel(dev, cli, "shell")
    Rel(gradio, service, "ask_stream(db_name, question, history)")
    Rel(cli, service, "ask(db_name, question)")
    Rel(service, agent, "agent.stream(MessagesState)")
    Rel(agent, llm_client, "model_with_tools.stream(messages)")
    Rel(agent, db_tools, "tool.invoke(args)")
    Rel(agent, mem_tools, "tool.invoke(args)")
    Rel(llm_client, openrouter, "POST /chat/completions (SSE)")
    Rel(db_tools, bird_db, "sqlite3.connect (read-only)")
    Rel(mem_tools, proc_db, "sqlite3.connect (read/write)")
    Rel(mem_tools, few_db, "sqlite3.connect (read/write)")
    Rel(mem_tools, openrouter, "POST /embeddings (sync)")
```

## Ключевые потоки

| Поток | Направление | Формат |
|-------|-------------|--------|
| Запрос пользователя | Gradio → Service → Agent | Python objects |
| Стриминг токенов | Agent → Service → Gradio | custom events: `{event, text}` |
| Tool calls | Agent → Toolkits → Agent | LangChain ToolMessage |
| LLM inference | ChatOpenRouter → OpenRouter | HTTPS SSE |
| Embeddings | MemoryToolkits → OpenRouter | HTTPS sync POST |
| DB queries | DatabaseToolkit → BIRD SQLite | SQLite read-only |
| Memory read/write | MemoryToolkits → SQLite stores | SQLite mutex-protected |
