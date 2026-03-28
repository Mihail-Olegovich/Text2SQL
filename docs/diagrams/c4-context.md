# C4 Context — Text2SQL Agent

Внешний вид системы: кто взаимодействует с ней и какие внешние сервисы она использует.

```mermaid
C4Context
    title Text2SQL Agent — System Context

    Person(user, "Пользователь", "Задаёт вопросы к БД\nна естественном языке")
    Person(dev, "Разработчик", "Запускает сервис,\nвалидирует качество на BIRD")

    System(text2sql, "Text2SQL Agent", "Принимает NL-вопрос,\nисследует схему БД,\nвозвращает SQL + результат")

    System_Ext(openrouter, "OpenRouter API", "LLM inference: Qwen3.5-397B\nEmbeddings: Qwen3-Embedding-8B")

    SystemDb_Ext(bird, "BIRD Benchmark", "95 SQLite-баз данных\ndev.json, dev_tables.json\n(37+ профессиональных доменов)")

    SystemDb(memory, "Локальная память", "SQLite: процедурные правила\n+ few-shot SQL-примеры")

    Rel(user, text2sql, "Вопрос + выбор БД", "HTTP / Gradio UI")
    Rel(dev, text2sql, "CLI-запрос", "shell")
    Rel(text2sql, openrouter, "Генерация SQL, векторизация запросов", "HTTPS REST")
    Rel(text2sql, bird, "Чтение схемы и данных", "SQLite read-only")
    Rel(text2sql, memory, "Запись и чтение памяти", "SQLite read/write")
```

## Границы системы

- **Text2SQL Agent** — единственная система в периметре; всё остальное внешнее
- **OpenRouter API** — внешний платный сервис; доступен только при наличии `OPEN_ROUTER_API_KEY`
- **BIRD Benchmark** — локальные данные, не включены в репозиторий (см. [data-setup.md](../data-setup.md))
- **Локальная память** — SQLite-файлы в `.text2sql/`; не синхронизируются между инстансами
