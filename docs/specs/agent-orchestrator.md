# Spec: Agent / Orchestrator

## Фреймворк

**LangGraph** `StateGraph` (версия `>= 1.0.10`).  
Граф компилируется один раз при старте `Text2SQLService` и многократно используется для разных запросов.

---

## Топология графа

```
START → llm_call → should_continue → tool_node → llm_call (цикл)
                         │
                         └────────────────────────────────→ END
```

| Элемент | Тип | Реализация |
|---------|-----|------------|
| `llm_call` | Node | `workflow.py: llm_call(state)` |
| `tool_node` | Node | `workflow.py: tool_node(state)` |
| `should_continue` | Conditional edge | `workflow.py: should_continue(state)` |

---

## Узел: `llm_call`

**Входное состояние:** `MessagesState`  
**Изменения состояния:** добавляет `AIMessage` в `messages`, инкрементирует `llm_calls`

**Алгоритм:**
1. Берёт `system_prompt` из `state` (или дефолтный `SYSTEM_PROMPT_TEMPLATE`)
2. Формирует входной список: `[SystemMessage(system_prompt)] + state["messages"]`
3. Вызывает `model_with_tools.stream(input_messages)`
4. Испускает события через `get_stream_writer()`:
   - `{event: "llm_start", llm_call_index: N}`
   - `{event: "llm_token", text: str}` — для каждого content-чанка
   - `{event: "llm_reasoning_token", text: str}` — для каждого reasoning-чанка
   - `{event: "llm_complete", has_tool_calls: bool, content: str, reasoning: str}`
5. Собирает `AIMessage` из накопленного стрима
6. Возвращает `{messages: [ai_message], llm_calls: state.llm_calls + 1}`

---

## Узел: `tool_node`

**Входное состояние:** `MessagesState` (последнее сообщение — `AIMessage` с `tool_calls`)  
**Изменения состояния:** добавляет список `ToolMessage` в `messages`

**Алгоритм:** для каждого `tool_call` в `state["messages"][-1].tool_calls`:

1. Испускает `{event: "tool_start", name: str, args: dict}`
2. Ищет инструмент в `tools_by_name` по `tool_call["name"]`
3. **Если инструмент не найден:**  
   `observation = "Tool input error for '{name}': unknown tool name. Available tools: ..."`
4. **Если инструмент найден:**  
   `observation = tool.invoke(tool_call["args"])` — или форматированное сообщение об ошибке при исключении
5. Испускает `{event: "tool_result", name: str, content: str}`
6. Добавляет `ToolMessage(content=str(observation), tool_call_id=tool_call["id"])`

**Выполнение:** последовательное (один tool_call за раз)

---

## Правила перехода (`should_continue`)

```python
def should_continue(state: MessagesState) -> Literal["tool_node", END]:
    if state.get("llm_calls", 0) >= max_llm_calls:
        return END
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        return "tool_node"
    return END
```

| Условие | Следующий узел |
|---------|----------------|
| `llm_calls >= max_llm_calls` | `END` (жёсткая остановка) |
| `last_message.tool_calls` не пустой | `tool_node` |
| `last_message.tool_calls` пустой | `END` (финальный ответ) |

---

## Stop conditions

| Условие | Тип | Поведение |
|---------|-----|-----------|
| LLM вернул ответ без tool_calls | Нормальный | Граф завершается, возвращается AIMessage |
| `llm_calls >= 20` | Hard limit | Граф завершается принудительно, возвращается последний AIMessage |

---

## Retry / Fallback

Явного механизма retry на уровне оркестратора нет. Self-correction реализован через LLM:

- `execute_sql` вернул `"SQL Error: ..."` → агент видит ошибку в `ToolMessage` и генерирует исправленный SQL
- Неверные аргументы инструмента → форматированное сообщение с `expected_args` → агент исправляет вызов
- Embedding недоступен → non-fatal строка → агент продолжает без few-shot примеров

**Нет retry для:**
- HTTP ошибок OpenRouter API (401, 429, 500) — исключение пробрасывается
- SQLite `FileNotFoundError` — исключение пробрасывается

---

## Инструкции агенту (системный промпт)

Системный промпт (`SYSTEM_PROMPT_TEMPLATE`) задаёт поведение агента:

| Правило | Описание |
|---------|----------|
| Вызов `search_sql_examples` | Обязателен перед генерацией SQL (`"Always call search_sql_examples before generating your own sql queries"`) |
| Избегать лишних `get_db_overview` | Не вызывать повторно, если схема уже известна из истории |
| Проверять значения перед WHERE | `execute_sql` для выборки реальных значений перед фильтрами |
| Фиксировать интерпретацию | При неоднозначном вопросе — сформулировать интерпретацию перед ответом |
| Запись в память | Только после явного подтверждения пользователя |

---

## Параметры конфигурации

| Параметр | Класс | Значение по умолчанию |
|----------|-------|-----------------------|
| `max_llm_calls` | `AgentSettings` | `20` |
| `model_name` | `ModelSettings` | `"qwen/qwen3.5-397b-a17b"` |
| `include_reasoning` | `ModelSettings` | `True` |
| `temperature` | `ModelSettings` | `None` (модельный дефолт) |
| `max_tokens` | `ModelSettings` | `None` (модельный дефолт) |
