# Подготовка данных (BIRD Benchmark)

Проект использует датасет [BIRD](https://bird-bench.github.io/) (Big Bench for Large-scale Database Grounded Text-to-SQL Evaluation). Данные не хранятся в репозитории — их нужно скачать вручную.

---

## Ожидаемая структура директории `data/`

```
data/
├── dev_20240627/
│   ├── dev_databases/
│   │   ├── california_schools/
│   │   │   ├── california_schools.sqlite
│   │   │   └── database_description/
│   │   │       ├── frpm.csv
│   │   │       ├── satscores.csv
│   │   │       └── schools.csv
│   │   ├── card_games/
│   │   │   └── ...
│   │   └── ... (всего 11 баз данных в dev)
│   ├── dev.json
│   └── dev_tables.json
└── train/
    ├── train_databases/
    │   └── ... (всего 69 баз данных в train)
    └── train_tables.json
```

Директория `data/` указана в `.gitignore` и не входит в репозиторий.

---

## Шаг 1. Регистрация и скачивание

1. Перейдите на официальный сайт BIRD: [https://bird-bench.github.io/](https://bird-bench.github.io/)
2. Нажмите кнопку **Download** в разделе **Dev Set** и **Train Set**
3. Либо воспользуйтесь прямыми ссылками из репозитория BIRD на GitHub:
   [https://github.com/AlibabaResearch/DAMO-ConvAI/tree/main/bird](https://github.com/AlibabaResearch/DAMO-ConvAI/tree/main/bird)

Вам понадобятся два архива:
- `dev_20240627.zip` — dev-выборка (11 баз данных)
- `train.zip` — тренировочная выборка (69 баз данных)

---

## Шаг 2. Распаковка

Распакуйте архивы в директорию `data/` в корне репозитория:

```bash
# Создать директорию data/
mkdir -p data

# Распаковать dev-выборку
unzip dev_20240627.zip -d data/

# Распаковать тренировочную выборку
unzip train.zip -d data/
```

После распаковки проверьте, что структура соответствует ожидаемой:

```bash
ls data/dev_20240627/
# dev.json  dev_databases/  dev_tables.json

ls data/train/
# train_databases/  train_tables.json
```

---

## Шаг 3. Генерация документации для баз данных

В датасете BIRD каждый вопрос снабжён полем `evidence` — кратким пояснением к данным. Скрипт `build_documentation.py` собирает эти подсказки по базам данных и записывает их в файлы `documentation.md`, которые агент использует при вызове `get_db_overview`.

```bash
poetry run python scripts/build_documentation.py
```

Скрипт создаст файл `documentation.md` в директории `database_description/` каждой базы данных:

```
data/dev_20240627/dev_databases/california_schools/database_description/documentation.md
data/dev_20240627/dev_databases/card_games/database_description/documentation.md
...
data/train/train_databases/*/database_description/documentation.md
```

> Если директории `database_description/` не существует для какой-либо базы, скрипт создаст её автоматически.

---

## Шаг 4. Проверка

Запустите агента с любым вопросом к одной из баз данных, чтобы убедиться, что данные загружены корректно:

```bash
poetry run python scripts/run_text2sql_service.py \
  --db-name california_schools \
  --question "How many schools are in the database?"
```

Или откройте веб-интерфейс:

```bash
poetry run python scripts/run_text2sql_web.py
```

В выпадающем списке должны появиться все доступные базы данных из `data/dev_20240627/dev_databases/`.

---

## Решение проблем

**`FileNotFoundError: dev_tables.json`**  
Проверьте, что файл находится по пути `data/dev_20240627/dev_tables.json`. Убедитесь, что архив был распакован именно в `data/`, а не в подпапку внутри `data/`.

**База данных не появляется в списке**  
Список баз формируется из директорий внутри `dev_databases/`. Убедитесь, что каждая база данных содержит `.sqlite`-файл с именем, совпадающим с именем директории (например, `california_schools/california_schools.sqlite`).

**Ошибка при генерации документации (`build_documentation.py`)**  
Скрипт читает `dev.json` и `train.json`. Убедитесь, что оба файла присутствуют: `data/dev_20240627/dev.json` и `data/train/train.json` (если тренировочная выборка скачана).
