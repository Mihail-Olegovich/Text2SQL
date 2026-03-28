# Spec: Retriever (Few-shot Memory)

## Назначение

Перед генерацией SQL агент ищет семантически похожие примеры вопрос→SQL из накопленной памяти.  
Релевантные примеры подставляются в контекст LLM через `ToolMessage`, снижая риск ошибок в SQL.

---

## Источник данных

| Параметр | Значение |
|----------|----------|
| Хранилище | `.text2sql/few_shot_memory.sqlite3` |
| Таблица | `few_shot_memory` |
| Namespace | `"few_shot_memory::{user_id}::{db_name}"` |

Схема таблицы:

```sql
CREATE TABLE IF NOT EXISTS few_shot_memory (
    namespace    TEXT NOT NULL,
    key          TEXT NOT NULL,       -- UUID, первичный ключ
    user_query   TEXT NOT NULL,
    sql_query    TEXT NOT NULL,
    note         TEXT NOT NULL,
    embedding_json TEXT NOT NULL,     -- JSON-сериализованный list[float]
    created_at   TEXT NOT NULL,       -- ISO 8601 UTC
    PRIMARY KEY (namespace, key)
)
```

---

## Индекс и хранение эмбеддингов

- Эмбеддинги хранятся как JSON-строка (`json.dumps(list[float])`) в колонке `embedding_json`
- Нет отдельного векторного индекса; все строки для namespace загружаются в память при поиске
- Размерность вектора определяется моделью `qwen/qwen3-embedding-8b` (динамическая)

---

## Embedding model

| Параметр | Значение |
|----------|----------|
| Модель | `qwen/qwen3-embedding-8b` |
| API endpoint | `https://openrouter.ai/api/v1/embeddings` |
| Auth | `Authorization: Bearer {OPEN_ROUTER_API_KEY}` |
| Timeout | 60 с |
| Формат запроса | `{"model": "...", "input": "текст"}` |
| Формат ответа | `{"data": [{"embedding": [float, ...]}]}` |

---

## Поиск (search)

**Триггер:** агент вызывает инструмент `search_sql_examples(db_name, user_query, limit=3)`.  
По инструкции системного промпта агент должен делать это перед каждой генерацией SQL.

**Алгоритм:**

```
1. embed("User question: {user_query}")  →  query_vector: list[float]
2. SELECT все строки WHERE namespace = ?  →  rows: list[tuple]
3. Для каждой строки: cosine_similarity(query_vector, stored_embedding)
4. Сортировка по убыванию score
5. Возврат top-k (limit, clamped в [1, 10])
```

**Cosine similarity (pure Python, без зависимостей):**

```python
dot = sum(a * b for a, b in zip(vec_a, vec_b))
norm_a = sqrt(sum(a*a for a in vec_a))
norm_b = sqrt(sum(b*b for b in vec_b))
score = dot / (norm_a * norm_b)  # -1.0 если нулевой вектор
```

**Выходной формат** (ToolMessage content):

```
Top 3 few-shot SQL examples for 'california_schools':

[1] similarity=0.912
Question: How many schools are in Los Angeles?
Note: district filter uses County column
SQL:
SELECT COUNT(*) FROM schools WHERE County = 'Los Angeles'

[2] similarity=0.847
...
```

---

## Запись (add)

**Триггер:** агент вызывает `add_sql_example(db_name, user_query, sql_query, note="")`.  
Только после явного подтверждения пользователя (контролируется системным промптом).

**Embedding input:**

```
"User question: {user_query}\nSQL pattern:\n{sql_query}\nNote: {note}"
```

Note: эмбеддируется комбинация всех трёх полей для лучшего recall при поиске.

**Upsert-политика:** `ON CONFLICT(namespace, key) DO UPDATE` — ключ = UUID, коллизии практически исключены.

---

## Ограничения

| Ограничение | Значение |
|-------------|----------|
| Сложность поиска | O(n) по числу примеров в namespace |
| Max limit при поиске | 10 (clamped в `FewShotMemoryToolkit`) |
| Timeout embedding | 60 с; превышение → non-fatal ToolMessage error |
| Нет ANN-индекса | При > 10k примеров поиск деградирует по latency |
| Нет reranking | Только cosine similarity, без cross-encoder |
| Scope | Только `(user_id, db_name)` — примеры не переносятся между БД |

---

## Failure modes

| Ситуация | Поведение |
|----------|-----------|
| Embedding API недоступен | `"Could not search few-shot examples: embedding request failed (...)"` → ToolMessage, агент продолжает без примеров |
| Нет примеров в namespace | `"No few-shot SQL examples found for this database."` → ToolMessage |
| Пустой `user_query` | `"Search query is empty."` → ToolMessage, до API-запроса не доходит |
| Пустой `user_query` или `sql_query` при записи | `"Example was not saved: ... is empty."` → ToolMessage |
| Embedding API вернул невалидный вектор | `ValueError("Embeddings response contains an invalid vector.")` → ToolMessage |
