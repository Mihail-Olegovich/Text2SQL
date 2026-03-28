# Spec: Tools / API

Контракты всех 6 инструментов агента.

---

## Обзор

| Инструмент | Модуль | Тип | Side effects |
|------------|--------|-----|--------------|
| `get_db_overview` | `DatabaseToolkit` | Read | Нет |
| `describe_table` | `DatabaseToolkit` | Read | Нет |
| `execute_sql` | `DatabaseToolkit` | Read | Нет |
| `add_procedural_memory` | `ProceduralMemoryToolkit` | Write | Upsert в SQLite |
| `search_sql_examples` | `FewShotMemoryToolkit` | Read + external API | Нет |
| `add_sql_example` | `FewShotMemoryToolkit` | Write + external API | Upsert в SQLite + вызов OpenRouter /embeddings |

---

## get_db_overview

**Назначение:** первичное исследование базы данных — список таблиц и доменная документация.

| Параметр | Тип | Описание |
|----------|-----|----------|
| `db_name` | `str` | Идентификатор БД (совпадает с именем директории в `dev_databases/`) |

**Возвращает:** `str`

```
Database: california_schools

SQLite tables:
  - frpm
  - satscores
  - schools

Documentation:
<содержимое database_description/documentation.md, если есть>
```

**Алгоритм:**
1. `sqlite3.connect(dev_db_path/{db_name}/{db_name}.sqlite)`
2. `SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'`
3. Чтение `database_description/documentation.md` (если файл существует)

**Errors:**

| Ситуация | Поведение |
|----------|-----------|
| SQLite-файл не найден | Exception пробрасывается как ToolMessage error |
| `documentation.md` отсутствует | В вывод добавляется: `"No documentation found for database '{db_name}'"` |

**Timeout:** нет (локальный SQLite)  
**Safety:** read-only, `sqlite3.connect` без `check_same_thread` limitation

---

## describe_table

**Назначение:** детали конкретной таблицы — схема колонок, PK/FK, CSV-описание значений.

| Параметр | Тип | Описание |
|----------|-----|----------|
| `db_name` | `str` | Идентификатор БД |
| `table_name` | `str` | Имя таблицы (case-insensitive при поиске CSV) |

**Возвращает:** `str`

```
SQLite schema for 'schools':
  CDSCode (TEXT, NOT NULL [PK])
  County (TEXT, NULL)
  ...

Primary keys:
  - CDSCode

Foreign keys (incoming):
  - frpm.CDSCode -> CDSCode

CSV description (schools.csv):
<первые строки CSV с описанием колонок>
```

**Алгоритм:**
1. `PRAGMA table_info("{table_name}")` — колонки, типы, nullable, PK
2. PK/FK из `dev_tables.json` (предзагружен в `self.db_schemas`)
3. Поиск CSV в `database_description/`: точное совпадение, затем case-insensitive

**Errors:**

| Ситуация | Поведение |
|----------|-----------|
| Таблица не существует в SQLite | PRAGMA возвращает пустой список; CSV-путь не найден → `"No SQLite table or CSV description found"` |
| CSV не найден | Блок с CSV-описанием пропускается |

---

## execute_sql

**Назначение:** выполнение SQL-запроса и возврат результата (не более 50 строк).

| Параметр | Тип | Описание |
|----------|-----|----------|
| `db_name` | `str` | Идентификатор БД |
| `sql_query` | `str` | SQL-запрос |

**Возвращает:** `str`

```
Database: california_schools

CDSCode | County | District
------- | ------ | --------
01234   | Alameda | Alameda Unified
...

... (results truncated to 50 rows)
```

**Алгоритм:**
1. `sqlite3.connect(...)`, `cursor.execute(sql_query)`
2. `fetchmany(51)` → если > 50 строк, добавляется `"... (results truncated to 50 rows)"`
3. Заголовок из `cursor.description`, строки в pipe-delimited формате

**Errors:**

| Ситуация | Поведение |
|----------|-----------|
| SQL Exception (синтаксис, неизвестная таблица/колонка) | `"SQL Error: {exc}"` — агент может исправить и повторить |
| `cursor.description is None` (non-SELECT) | `"Query returned no results"` |

**Safety:**
- Соединение закрывается в `finally`; нет пула соединений
- DDL/DML блокируется системным промптом и описанием инструмента (`"read-only"`)
- Нет явного запрета на уровне кода — защита через prompt engineering

**Timeout:** нет (локальный SQLite)

---

## add_procedural_memory

**Назначение:** сохранить короткую текстовую подсказку (правило, кодировку, домейн-логику) для текущего пользователя и БД.

| Параметр | Тип | Описание |
|----------|-----|----------|
| `db_name` | `str` | Идентификатор БД |
| `memory` | `str` | Короткий текстовый хинт |

**Возвращает:** `str` — `"Memory saved for db '{db_name}'."` или сообщение об ошибке

**Алгоритм:**
1. Валидация: `memory.strip()` не пустой
2. `namespace = ("procedural_memory", user_id, db_name)`
3. `SQLiteProceduralMemoryStore.put(namespace, uuid4(), {memory, db_name, user_id, created_at})`
4. Upsert: `ON CONFLICT DO UPDATE` (по `(namespace, key)`) — при совпадении UUID маловероятно

**Side effects:** запись в `.text2sql/procedural_memory.sqlite3`  
**Concurrency:** `threading.Lock` при записи  
**Safety:** только write к локальному SQLite; никаких внешних вызовов

**Errors:**

| Ситуация | Поведение |
|----------|-----------|
| Пустая строка `memory` | `"Memory was not saved: memory is empty."` |
| SQLite write error | Exception пробрасывается как ToolMessage error |

---

## search_sql_examples

**Назначение:** семантический поиск похожих примеров вопрос→SQL перед генерацией нового SQL.

| Параметр | Тип | Default | Описание |
|----------|-----|---------|----------|
| `db_name` | `str` | — | Идентификатор БД |
| `user_query` | `str` | — | Текущий вопрос пользователя |
| `limit` | `int` | `3` | Число примеров; clamped в `[1, 10]` |

**Возвращает:** `str` — форматированный список примеров с similarity score

**External API call:** `POST https://openrouter.ai/api/v1/embeddings` (timeout: 60 с)

**Errors:**

| Ситуация | Поведение |
|----------|-----------|
| Embedding API недоступен | `"Could not search few-shot examples: embedding request failed (...)"` — non-fatal |
| Нет примеров в namespace | `"No few-shot SQL examples found for this database."` |
| Пустой `user_query` | `"Search query is empty."` |

**Side effects:** нет

---

## add_sql_example

**Назначение:** сохранить успешную пару вопрос→SQL для будущего повторного использования.

| Параметр | Тип | Default | Описание |
|----------|-----|---------|----------|
| `db_name` | `str` | — | Идентификатор БД |
| `user_query` | `str` | — | Вопрос пользователя |
| `sql_query` | `str` | — | Корректный SQL-запрос |
| `note` | `str` | `""` | Дополнительное пояснение |

**Возвращает:** `str` — `"Few-shot example saved for db '{db_name}'."` или сообщение об ошибке

**External API call:** `POST https://openrouter.ai/api/v1/embeddings` (timeout: 60 с)  
**Side effects:** запись в `.text2sql/few_shot_memory.sqlite3`

**Errors:**

| Ситуация | Поведение |
|----------|-----------|
| Embedding API failure | `"Example was not saved: embedding request failed (...)"` |
| Пустые `user_query` или `sql_query` | `"Example was not saved: ... is empty."` |

---

## Общие свойства всех инструментов

- Все инструменты зарегистрированы через LangChain `@tool` и передаются модели через `model.bind_tools(tools)`
- Неизвестный инструмент в `tool_node`: `"Tool input error: unknown tool name. Available tools: ..."`
- Исключение при вызове (неверные аргументы): форматированное сообщение с `tool_args` и `expected_args` из схемы Pydantic
- Нет глобального timeout для DB-инструментов (локальный SQLite, latency пренебрежима)
