# Workflow Diagram — Выполнение запроса

Пошаговое выполнение запроса от пользователя до финального ответа, включая ветки ошибок.

```mermaid
flowchart TD
    A([Вход: db_name + question + history]) --> B[Text2SQLService.ask_stream]

    B --> C["_format_memories_for_prompt\nProceduralMemoryStore.search\n(procedural_memory, user_id, db_name)"]
    C --> D["render_system_prompt\nправила → системный промпт"]
    D --> E["_build_request_state\nmessages = history + HumanMessage\nMessagesState = {messages, system_prompt}"]

    E --> F[[LangGraph: START → llm_call]]

    subgraph LLM ["llm_call node"]
        F --> G["[SystemMessage(system_prompt)] + messages\n→ ChatOpenRouter.stream()"]
        G --> G1[/"event: llm_start {llm_call_index}"/]
        G1 --> G2[/"events: llm_token, llm_reasoning_token (stream)"/]
        G2 --> G3["Сборка AIMessage\ncontent + tool_calls + reasoning"]
        G3 --> G4[/"event: llm_complete {has_tool_calls, content, reasoning}"/]
    end

    G4 --> H{"should_continue"}

    H -- "tool_calls = [] OR llm_calls >= 20" --> Z(["END\nвернуть AIMessage\n(content + reasoning + trace)"])

    H -- "tool_calls present AND llm_calls < 20" --> I

    subgraph TOOLS ["tool_node"]
        I["Для каждого tool_call в порядке"] --> J[/"event: tool_start {name, args}"/]
        J --> K{"Инструмент\nсуществует?"}

        K -- "Нет" --> ERR1["ToolMessage:\nunknown tool + список доступных"]

        K -- "Да" --> L{"Тип инструмента"}

        L -- "execute_sql" --> M["sqlite3.connect(db_name.sqlite)\ncursor.execute(sql_query)\nfetchmany(51), limit 50"]
        M -- "SQL Exception" --> SQLERR["ToolMessage:\n'SQL Error: {exc}'"]
        M -- "OK" --> SQLOK["ToolMessage:\npipe-delimited rows\n+ '... truncated' если > 50"]

        L -- "get_db_overview\ndescribe_table" --> N["sqlite3.connect + PRAGMA\n+ CSV read + JSON schema"]
        N -- "FileNotFoundError" --> DBERR["ToolMessage:\n'SQL Error: ...' или исключение"]
        N -- "OK" --> DBOK["ToolMessage:\nтекст описания"]

        L -- "search_sql_examples" --> O["embed(user_query)\n→ cosine similarity\n→ top-k matches"]
        O -- "embed failed" --> EMBERR["ToolMessage:\n'Could not search: embed failed'"]
        O -- "OK, matches = []" --> EMPTY["ToolMessage:\n'No examples found'"]
        O -- "OK, matches > 0" --> FOUND["ToolMessage:\nотформатированные примеры\nс similarity score"]

        L -- "add_sql_example" --> P["embed(query+sql+note)\n→ SQLite upsert"]
        P -- "embed failed" --> ADDERR["ToolMessage:\n'Example was not saved: embed failed'"]
        P -- "OK" --> ADDOK["ToolMessage:\n'Few-shot example saved'"]

        L -- "add_procedural_memory" --> Q["SQLite upsert\n(procedural_memory, user, db)"]
        Q --> MEMOK["ToolMessage:\n'Memory saved'"]

        SQLERR --> R
        SQLOK --> R
        DBERR --> R
        DBOK --> R
        EMBERR --> R
        EMPTY --> R
        FOUND --> R
        ADDERR --> R
        ADDOK --> R
        MEMOK --> R
        ERR1 --> R

        R[/"event: tool_result {name, content}"/]
        R --> S{"Ещё tool_calls\nв этом шаге?"}
        S -- "Да" --> I
        S -- "Нет" --> T["messages += ToolMessage[]"]
    end

    T --> F

    style Z fill:#90EE90,color:#000
    style SQLERR fill:#FFD700,color:#000
    style DBERR fill:#FFD700,color:#000
    style EMBERR fill:#FFD700,color:#000
    style ADDERR fill:#FFD700,color:#000
    style ERR1 fill:#FFD700,color:#000
```

## Замечания по ошибкам

- Все ошибки инструментов **не прерывают граф** — они возвращаются агенту как строки внутри `ToolMessage`
- Агент видит ошибку SQL и может попробовать исправленный запрос (self-correction)
- Исключения при вызове инструментов с неправильными аргументами форматируются с `expected_args`
- Единственный hard stop — достижение `llm_calls >= max_llm_calls = 20`
- Ошибки embedding при `search_sql_examples` non-fatal: агент продолжает работу без примеров
