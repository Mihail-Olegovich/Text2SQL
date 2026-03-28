# Spec: Memory / Context

## Session State

LangGraph граф оперирует единственным объектом состояния `MessagesState`:

```python
class MessagesState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]  # append-only
    llm_calls: int
    system_prompt: str
```

| Поле | Тип | Семантика | Кто изменяет |
|------|-----|-----------|--------------|
| `messages` | `list[AnyMessage]` | Вся история диалога внутри запроса: `HumanMessage`, `AIMessage`, `ToolMessage`. Append-only через `operator.add` | `llm_call` (добавляет AIMessage), `tool_node` (добавляет ToolMessage[]) |
| `llm_calls` | `int` | Счётчик вызовов LLM; stop condition при ≥ 20 | `llm_call` (инкрементирует) |
| `system_prompt` | `str` | Системный промпт, отрендеренный перед стартом графа; переопределяет дефолтный | `Text2SQLService._build_request_state()` |

**Системный промпт** НЕ хранится в `messages`. На каждом шаге `llm_call` добавляет `SystemMessage(system_prompt)` в начало списка сообщений перед передачей в модель.

---

## Multi-turn контекст (между запросами)

`MessagesState` создаётся заново для каждого вызова `ask` / `ask_stream`. История передаётся снаружи:

```python
Text2SQLService.ask_stream(
    db_name="...",
    question="...",
    history=[HumanMessage(...), AIMessage(...), ...]  # из предыдущего хода
)
```

В Gradio UI история хранится как `gr.State`:

```python
lc_history: list[BaseMessage]  # накапливается после каждого хода
```

### Reasoning-инъекция

Модели с reasoning (`include_reasoning=True`) возвращают поле `reasoning` в ответе.  
После каждого хода оно сохраняется в `AIMessage.additional_kwargs["reasoning"]`.

`ChatOpenRouter._format_messages()` при форматировании prior assistant-сообщений добавляет:

```python
# Prior AIMessage → list of content blocks:
[
    {"type": "text", "text": "<prior_reasoning>"},      # если reasoning есть
    {"type": "text", "text": "<prior_trace_json>"},     # если trace есть (JSON шагов)
    {"type": "text", "text": "<content>"},              # основной ответ
]
```

Это позволяет модели в следующем ходу видеть полную цепочку рассуждений предыдущего хода.

---

## Процедурная память

### Политика загрузки

- **Когда:** перед каждым вызовом агента, в `Text2SQLService._build_request_state()`
- **Что:** все записи для `namespace = ("procedural_memory", user_id, db_name)`
- **Limit:** 100 записей (последние по `created_at DESC`)

### Формат в промпте

```
## Session context

Stored memory for this database and this user:
- male patients use SEX = 'M'
- ALT exceeds normal when GPT >= 60
- ...
```

При отсутствии записей: `"- No stored procedural memories yet."`

### Политика записи

- Только после явного подтверждения пользователя (инструкция в системном промпте)
- Агент предлагает сохранить: `"I could save the rule that... — want me to?"`
- Вызов `add_procedural_memory` только после `"yes"` / `"да"` от пользователя

### Хранение

| Параметр | Значение |
|----------|----------|
| Файл | `.text2sql/procedural_memory.sqlite3` |
| Namespace format | `"procedural_memory::{user_id}::{db_name}"` |
| Ключ | UUID v4 (новый при каждой записи) |
| Значение | `{memory: str, db_name: str, user_id: str, created_at: ISO8601}` |

---

## Few-shot память

### Политика загрузки

- **Когда:** по запросу агента (`search_sql_examples`) во время работы графа
- **Триггер:** системный промпт инструктирует вызывать поиск перед каждой генерацией SQL
- **Алгоритм:** cosine similarity (см. [specs/retriever.md](retriever.md))

### Политика записи

- Только после явного подтверждения пользователя
- `add_sql_example` вызывается с `user_query`, `sql_query`, `note`
- Embedding вычисляется немедленно; хранится рядом с примером

---

## Context budget

| Ресурс | Лимит | Механизм |
|--------|-------|----------|
| Итерации агента | 20 | `max_llm_calls` в `AgentSettings` |
| Строки SQL-результата | 50 | `execute_sql` → `fetchmany(51)` |
| Процедурных правил в промпте | 100 | `SQLiteProceduralMemoryStore.search(limit=100)` |
| Few-shot примеров за один поиск | 10 | `FewShotMemoryToolkit` (clamped `safe_limit = max(1, min(limit, 10))`) |
| Max tokens | Не задан | `ModelSettings.max_tokens = None` |
| Context window | Определяется моделью | Вся история передаётся целиком без усечения |

> **PoC-ограничение:** токенный бюджет явно не контролируется. При очень длинных историях диалога возможно превышение context window модели. В production необходимо добавить trimming или summarization истории.
