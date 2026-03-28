# Data Flow Diagram

Как данные проходят через систему: что читается, что записывается, что логируется.

```mermaid
flowchart LR
    subgraph IN ["Вход"]
        U[/"Пользователь:\ndb_name\nquestion\nlc_history"/]
    end

    subgraph SVC ["Text2SQLService"]
        MEM_LOAD["ProceduralMemoryStore.search\n(procedural_memory, user_id, db_name)\n→ rules: list[dict]"]
        PROMPT["render_system_prompt(rules)\n→ system_prompt: str"]
        STATE["MessagesState:\n{messages: history + HumanMessage,\n system_prompt: str,\n llm_calls: 0}"]
    end

    subgraph AGENT ["LangGraph Agent"]
        LLM_IN["[SystemMessage(system_prompt)]\n+ messages\n→ ChatOpenRouter.stream()"]
        LLM_OUT["AIMessage:\ncontent: str\ntool_calls: list\nreasoning: str\ntrace: JSON str"]
        TOOL_IN["tool_call:\n{name, args, id}"]
        TOOL_OUT["ToolMessage:\n{content: str,\n tool_call_id: str}"]
    end

    subgraph TOOLS ["Tool Layer"]
        DB_TOOL["execute_sql / get_db_overview\n/ describe_table\n→ текстовый результат"]
        MEM_W["add_procedural_memory\n→ upsert в SQLite"]
        FS_SEARCH["search_sql_examples:\nembed(query) → cosine → top-k\n→ форматированный список"]
        FS_ADD["add_sql_example:\nembed(q+sql+note) → upsert\n→ подтверждение"]
    end

    subgraph EXT ["Внешние вызовы"]
        OR_LLM["OpenRouter\nPOST /chat/completions\n(SSE stream)\nqwen/qwen3.5-397b-a17b"]
        OR_EMBED["OpenRouter\nPOST /embeddings\n(sync, timeout=60s)\nqwen/qwen3-embedding-8b"]
    end

    subgraph STORAGE ["Хранилища"]
        BIRD_DB[("BIRD SQLite\ndata/dev_20240627/dev_databases/\nread-only")]
        PROC_DB[(".text2sql/\nprocedural_memory.sqlite3\nread/write")]
        FS_DB[(".text2sql/\nfew_shot_memory.sqlite3\nread/write")]
    end

    subgraph OUT ["Выход"]
        EVENTS[/"Stream events:\nllm_token\nllm_reasoning_token\ntool_start\ntool_result"/]
        FINAL[/"Финальный AIMessage\n→ content (ответ пользователю)\n→ reasoning + trace\n   (→ lc_history следующего хода)"/]
    end

    U --> MEM_LOAD
    MEM_LOAD --> PROC_DB
    PROC_DB --> MEM_LOAD
    MEM_LOAD --> PROMPT
    PROMPT --> STATE
    U --> STATE

    STATE --> LLM_IN
    LLM_IN --> OR_LLM
    OR_LLM --> LLM_OUT
    LLM_OUT --> TOOL_IN
    LLM_OUT --> FINAL

    TOOL_IN --> DB_TOOL
    TOOL_IN --> MEM_W
    TOOL_IN --> FS_SEARCH
    TOOL_IN --> FS_ADD

    DB_TOOL --> BIRD_DB
    BIRD_DB --> DB_TOOL
    DB_TOOL --> TOOL_OUT

    MEM_W --> PROC_DB
    MEM_W --> TOOL_OUT

    FS_SEARCH --> OR_EMBED
    OR_EMBED --> FS_SEARCH
    FS_SEARCH --> FS_DB
    FS_DB --> FS_SEARCH
    FS_SEARCH --> TOOL_OUT

    FS_ADD --> OR_EMBED
    OR_EMBED --> FS_ADD
    FS_ADD --> FS_DB
    FS_ADD --> TOOL_OUT

    TOOL_OUT --> LLM_IN

    LLM_OUT --> EVENTS
    TOOL_OUT --> EVENTS
    FINAL --> U
```

## Что хранится

| Данные | Хранилище | Время жизни | Формат |
|--------|-----------|-------------|--------|
| Процедурные правила | `.text2sql/procedural_memory.sqlite3` | Постоянно (между сессиями) | JSON `{memory, db_name, user_id, created_at}` |
| Few-shot примеры | `.text2sql/few_shot_memory.sqlite3` | Постоянно (между сессиями) | `(user_query, sql_query, note, embedding_json)` |
| История диалога | `gr.State` в Gradio UI | Текущая сессия браузера | `list[BaseMessage]` |
| Reasoning + trace | `AIMessage.additional_kwargs` | Текущий ход → передаётся в следующий | `str` (reasoning), `str` (JSON trace) |

## Что логируется

| Событие | Тип | Получатель |
|---------|-----|------------|
| `llm_token` | Streaming event | Gradio UI (отображается в реальном времени) |
| `llm_reasoning_token` | Streaming event | Gradio UI (сворачиваемый блок "Thinking #N") |
| `tool_start` | Streaming event | Gradio UI (блок "Using tool X" с аргументами) |
| `tool_result` | Streaming event | Gradio UI (содержимое ответа инструмента) |
| `llm_complete` | Streaming event | Gradio UI (финализация шага) |

**Не логируется:** token usage, request latency, error rate, стоимость запросов.
