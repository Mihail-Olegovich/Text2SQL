# Spec: Serving / Config

## Точки входа


| Скрипт        | Команда                                                                             | Режим                                                                  |
| ------------- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Веб-интерфейс | `poetry run python scripts/run_text2sql_web.py`                                     | Streaming, Gradio UI на [http://localhost:7860](http://localhost:7860) |
| CLI           | `poetry run python scripts/run_text2sql_service.py --db-name <db> --question "<q>"` | Синхронный, вывод в stdout                                             |


---

## Конфигурация (`ServiceSettings`)

Единственная точка входа: `ServiceSettings.defaults()` — создаёт все вложенные датаклассы из окружения и путей по умолчанию.

```python
ServiceSettings.defaults() = ServiceSettings(
    data_paths = DataPaths.defaults(),
    model      = ModelSettings.from_env(),
    agent      = AgentSettings(),
    memory     = MemorySettings.defaults(),
)
```

Все датаклассы `frozen=True` — после инициализации конфигурация неизменна.

---

## Секреты


| Переменная            | Обязательность | Описание                                                         |
| --------------------- | -------------- | ---------------------------------------------------------------- |
| `OPEN_ROUTER_API_KEY` | Обязательная   | API-ключ OpenRouter; используется для LLM inference и embeddings |


Загрузка: `python-dotenv` через `load_dotenv()` в `ModelSettings.from_env()`. Ищет `.env` в рабочей директории.  
Если ключ отсутствует: `ValueError("OPEN_ROUTER_API_KEY is not set in the environment.")`.

Шаблон: `.env.example` в корне репозитория.

---

## Пути к данным (`DataPaths`)

Все пути относительны к корню репозитория (`Path(__file__).resolve().parents[2]`).


| Параметр            | Путь по умолчанию                   |
| ------------------- | ----------------------------------- |
| `dev_db_path`       | `data/dev_20240627/dev_databases/`  |
| `dev_tables_path`   | `data/dev_20240627/dev_tables.json` |
| `dev_json_path`     | `data/dev_20240627/dev.json`        |
| `train_db_path`     | `data/train/train_databases/`       |
| `train_tables_path` | `data/train/train_tables.json`      |


Данные не входят в репозиторий. Инструкция по загрузке: [data-setup.md](../data-setup.md).

---

## Настройки модели (`ModelSettings`)


| Параметр            | Значение по умолчанию                             | Описание                              |
| ------------------- | ------------------------------------------------- | ------------------------------------- |
| `model_name`        | `"qwen/qwen3.5-397b-a17b"`                        | Модель для inference через OpenRouter |
| `base_url`          | `"https://openrouter.ai/api/v1/chat/completions"` | Endpoint для inference                |
| `include_reasoning` | `True`                                            | Извлекать поле `reasoning` из ответа  |
| `temperature`       | `None`                                            | Передаётся только если не `None`      |
| `max_tokens`        | `None`                                            | Передаётся только если не `None`      |
| `api_key`           | Из `OPEN_ROUTER_API_KEY`                          |                                       |


---

## Настройки памяти (`MemorySettings`)


| Параметр               | Значение по умолчанию                       | Описание                                        |
| ---------------------- | ------------------------------------------- | ----------------------------------------------- |
| `sqlite_path`          | `.text2sql/procedural_memory.sqlite3`       | Процедурная память                              |
| `few_shot_sqlite_path` | `.text2sql/few_shot_memory.sqlite3`         | Few-shot память                                 |
| `embedding_model`      | `"qwen/qwen3-embedding-8b"`                 | Модель эмбеддингов                              |
| `embedding_base_url`   | `"https://openrouter.ai/api/v1/embeddings"` | Endpoint для эмбеддингов                        |
| `user_id`              | `"default_user"`                            | Идентификатор пользователя для namespace памяти |


SQLite-файлы создаются автоматически при первом запуске .

---

## Настройки агента (`AgentSettings`)


| Параметр        | Значение по умолчанию | Описание                                        |
| --------------- | --------------------- | ----------------------------------------------- |
| `max_llm_calls` | `20`                  | Максимальное число итераций LLM в одном запросе |


---



