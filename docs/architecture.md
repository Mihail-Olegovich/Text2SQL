# Архитектура системы

## Обзор

Text2SQL Agent — это многошаговый агент на базе [LangGraph](https://langchain-ai.github.io/langgraph/), который принимает вопрос на естественном языке, самостоятельно исследует схему базы данных и возвращает SQL-запрос с результатом выполнения.

Компоненты системы:

```
scripts/run_text2sql_web.py   # веб-интерфейс на Gradio
scripts/run_text2sql_service.py  # CLI-запуск

text2sql/service.py           # Text2SQLService — точка входа
text2sql/agent/workflow.py    # LangGraph StateGraph
text2sql/llm/openrouter.py    # ChatOpenRouter (кастомная LangChain-модель)
text2sql/tools/db_tools.py    # DatabaseToolkit (инструменты для БД)
text2sql/tools/memory_tools/  # ProceduralMemoryToolkit + FewShotMemoryToolkit
text2sql/config/settings.py   # конфигурация (DataPaths, ModelSettings, ...)
text2sql/prompts.py           # системный промпт
```

---

## Граф агента (LangGraph)

Агент реализован как `StateGraph` с двумя узлами:

```
         ┌──────────────────────────────┐
         │                              │
         ▼                              │
   ┌───────────┐   есть tool_calls   ┌──────────┐
   │  llm_call │ ──────────────────► │tool_node │
   └───────────┘                     └──────────┘
         │
         │ нет tool_calls или достигнут лимит llm_calls
         ▼
        END
```

**Состояние графа (`MessagesState`):**

| Поле          | Тип                      | Описание                                        |
|---------------|--------------------------|-------------------------------------------------|
| `messages`    | `list[BaseMessage]`      | Вся история диалога (append-only)               |
| `llm_calls`   | `int`                    | Счётчик вызовов LLM                             |
| `system_prompt` | `str`                  | Системный промпт, собранный перед запросом      |

**Узел `llm_call`:**
- Вызывает `ChatOpenRouter` в потоковом режиме
- Испускает кастомные события: `llm_start`, `llm_token`, `llm_reasoning_token`, `llm_complete`
- Инкрементирует счётчик `llm_calls`

**Узел `tool_node`:**
- Выполняет все tool-вызовы из последнего сообщения LLM
- Перехватывает ошибки вызова (неизвестный инструмент, исключения) и возвращает их в сообщение
- Испускает события: `tool_start`, `tool_result`

**Переход `should_continue`:**
- Если последнее сообщение содержит tool calls и `llm_calls < max_llm_calls` → `tool_node`
- Иначе → `END`

---

## Языковая модель

Класс `ChatOpenRouter` (`text2sql/llm/openrouter.py`) — кастомная реализация `BaseChatModel`, работающая напрямую с HTTP API OpenRouter (без использования пакета `langchain-openai`).

Ключевые особенности:
- Поддержка потокового режима (SSE)
- Извлечение `reasoning` из ответа модели и сохранение его в `additional_kwargs`
- Инъекция предыдущих `reasoning` и `trace` блоков в контекст следующего хода (многоходовой диалог)
- `bind_tools()` конвертирует инструменты в формат OpenAI tools

Модель по умолчанию: `qwen/qwen3.5-397b-a17b` с включённым reasoning.

---

## Инструменты агента

Агент имеет доступ к шести инструментам, разбитым на три группы.

### Инструменты базы данных (`DatabaseToolkit`)

| Инструмент      | Сигнатура                              | Описание                                                               |
|-----------------|----------------------------------------|------------------------------------------------------------------------|
| `get_db_overview` | `(db_name: str) -> str`             | Список таблиц БД + документация из `documentation.md`, если есть      |
| `describe_table`  | `(db_name: str, table_name: str) -> str` | Схема таблицы (PRAGMA table_info), PK/FK, CSV-описание колонок    |
| `execute_sql`     | `(db_name: str, sql_query: str) -> str` | Выполнение SELECT-запроса; результат — не более 50 строк           |

Безопасность:
- Соединения с SQLite открываются только на чтение
- DDL/DML-команды (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`) блокируются на уровне промпта и описания инструмента

### Инструменты процедурной памяти (`ProceduralMemoryToolkit`)

| Инструмент              | Сигнатура                                    | Описание                                              |
|-------------------------|----------------------------------------------|-------------------------------------------------------|
| `add_procedural_memory` | `(db_name: str, memory: str) -> str`         | Сохранить текстовую подсказку (правила, кодировки, ...) |

Хранилище: SQLite (`procedural_memory.sqlite3`), пространство имён `(type, user_id, db_name)`.

При каждом запросе к агенту все записи для текущей пары `(user_id, db_name)` подставляются в системный промпт.

### Инструменты few-shot памяти (`FewShotMemoryToolkit`)

| Инструмент            | Сигнатура                                                                | Описание                                                  |
|-----------------------|--------------------------------------------------------------------------|-----------------------------------------------------------|
| `search_sql_examples` | `(db_name: str, user_query: str, limit: int = 3) -> str`                | Семантический поиск похожих примеров (cosine similarity)  |
| `add_sql_example`     | `(db_name: str, user_query: str, sql_query: str, note: str) -> str`     | Сохранить пару (вопрос, SQL) с эмбеддингом               |

Хранилище: SQLite (`few_shot_memory.sqlite3`).  
Модель эмбеддингов: `qwen/qwen3-embedding-8b` через OpenRouter Embeddings API.  
Поиск: brute-force cosine similarity по всем сохранённым примерам для данной БД.

---

## Система памяти

```
Запрос пользователя
        │
        ▼
ProceduralMemoryToolkit.load()
  → подставляет правила в системный промпт (до старта агента)
        │
        ▼
  LangGraph агент
        │
        ├── search_sql_examples()  ← агент вызывает сам перед написанием SQL
        │       └── FewShotMemoryStore.search() (cosine similarity)
        │
        └── (по запросу пользователя, после подтверждения)
                ├── add_procedural_memory()  → сохраняет правило
                └── add_sql_example()        → сохраняет пример с эмбеддингом
```

Обе базы персистентны между сессиями и хранятся в `.text2sql/`.

---

## Конфигурация (`ServiceSettings`)

`ServiceSettings.defaults()` собирает все настройки из окружения и дефолтных путей:

| Класс             | Ключевые параметры                                                        |
|-------------------|---------------------------------------------------------------------------|
| `DataPaths`       | Пути к файлам BIRD: `dev_databases/`, `dev_tables.json`, `dev.json`       |
| `ModelSettings`   | `model_name`, `base_url`, `include_reasoning`, `temperature`, `max_tokens`; API-ключ из `OPEN_ROUTER_API_KEY` |
| `AgentSettings`   | `max_llm_calls = 20`                                                      |
| `MemorySettings`  | Пути к SQLite-файлам памяти, модель эмбеддингов, `user_id`                |

---

## Веб-интерфейс (`scripts/run_text2sql_web.py`)

Реализован на [Gradio](https://www.gradio.app/) (`gr.Blocks`):

- Выпадающий список с названиями доступных баз данных
- Чат-окно с потоковым выводом токенов
- Сворачиваемые блоки "Thinking #N" для reasoning-цепочек
- Блоки "Using tool `X`" с тайммингом для каждого вызова инструмента
- История диалога (`lc_history`) передаётся в LangGraph-состояние между ходами

---

## CLI (`scripts/run_text2sql_service.py`)

```bash
poetry run python scripts/run_text2sql_service.py --db-name <db> --question "<question>"
```

Синхронный запуск: инициализирует `Text2SQLService`, вызывает `service.ask()`, выводит финальный ответ.

---

## Диаграмма потока данных

```
Пользователь
     │  вопрос + db_name
     ▼
Text2SQLService.ask_stream()
     │  1. загружает процедурные правила для db_name
     │  2. рендерит системный промпт
     │  3. добавляет историю диалога
     ▼
LangGraph StateGraph.stream()
     │
     ├── llm_call ──► ChatOpenRouter (OpenRouter HTTP API)
     │                      │  streaming SSE
     │                      ▼ tool_calls
     ├── tool_node
     │       ├── get_db_overview    ──► SQLite (read-only)
     │       ├── describe_table     ──► SQLite + CSV files
     │       ├── execute_sql        ──► SQLite (read-only)
     │       ├── add_procedural_memory ──► .text2sql/procedural_memory.sqlite3
     │       ├── search_sql_examples   ──► .text2sql/few_shot_memory.sqlite3
     │       └── add_sql_example       ──► OpenRouter Embeddings API
     │                                     + .text2sql/few_shot_memory.sqlite3
     │
     └── END
           │  events: llm_token, tool_start, tool_result, ...
           ▼
Gradio UI (потоковый вывод)
```
