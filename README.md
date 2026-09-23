# RedOrda · Beeline Campaign Planner

Планирование тарифных маркетинговых кампаний: аудитория → гипотезы → пилоты → выбор кампаний с оценкой прироста выручки и стоимости контактов.

**Стек:** Django 5.2, DRF, React **18.2.0**, TypeScript, PostgreSQL, Redis, Celery. Общий Python-движок для веба и `Agent.act(env)`. OpenAI Responses API, модель по умолчанию `gpt-6-sol`, настраивается через `.env`.

## Три разработчика, три этапа

| Разработчик | Этап 1: основа | Этап 2: сценарий | Этап 3: проверка |
| --- | --- | --- | --- |
| [1. Backend](docs/developers/01-backend.md) | Данные, модели, API | Celery, прогресс, результаты | Интеграция, ошибки, Docker |
| [2. AI-агент](docs/developers/02-agent.md) | Базовая стратегия и пилоты | GPT-6, разведка, оптимизация | Оценка, лимиты, submission |
| [3. Frontend](docs/developers/03-frontend.md) | Экраны и API-клиент | Запуск, прогресс, таблица | Адаптация, проверка, демо |

[Общий API-контракт](docs/api-contract.md).

## Что уже есть

Интегрированы четыре backend-ветки: проверка и импорт семи CSV, модели и миграции,
Django sessions/CSRF, очередь Celery, идемпотентный старт, отмена, журнал событий,
сохранённые результаты и CSV. PostgreSQL, Redis, Gunicorn, worker и frontend описаны
в Compose с healthchecks. [Шаблон CI](infra/ci/github-actions.yaml) проверяет
PostgreSQL, живую очередь и сборку Compose. Действующий workflow удалён параллельным
коммитом main; его удаление сохранено. Автодеплой Mac mini продолжает запускать `make check`.

**AI-движок:** общий runner и `Agent.act`, анализ истории, адаптивные пилоты, оценки неопределённости, выбор каналов, проверка лимитов, события и отмена, GPT-6 с fallback/replay, воспроизводимый CSV и сборка автономного агента. [Контракт runner и подключение backend](docs/developers/02-agent-integration.md).

**Веб-сценарий:** вход/CSRF → создание плана → общий runner с заданными лимитами →
пилоты и прогресс → сохранённые кампании и прогноз → CSV. Отмена и повтор старта
без дублирования поддерживаются. Backend проверяется также на официальном локальном
симуляторе из пакета организаторов, без подмены runner.
Это учебная симуляция, не судейская среда: прогноз не выдаётся за конкурсный результат.
На сервере необходимо импортировать пакет, включить расчёты и запустить Redis/worker;
проверка развёрнутого сайта и конкурсная оценка — отдельные шаги.
[Отчёт интеграции и проверки](docs/backend-integration.md).
[Публичный сайт](https://hack.1ge.kz), [автодеплой на Mac mini](docs/deployment.md).

Документация без входа: [Swagger](https://hack.1ge.kz/api/docs/) и
[OpenAPI JSON](https://hack.1ge.kz/api/schema/?format=json).

## Локальный запуск

Python 3.11+ и Node.js 20.19+ (рекомендуются Python 3.12 и Node.js 22).

```bash
cp .env.example .env
make setup
make migrate
```

Для просмотра достаточно двух терминалов: `make backend` и `make frontend`.
Для очереди запустите PostgreSQL и Redis, укажите `DATABASE_URL` и `REDIS_URL` в `.env`,
повторите `make migrate`, затем выполните `make worker` в третьем терминале.
Создание оператора: `.venv/bin/python backend/manage.py createsuperuser` (пароль вводится вручную).
Для проверки входа локально задайте `REDORDA_REQUIRE_AUTH=1`.

В `.env.example` включён режим локальной симуляции (`REDORDA_RUN_EXECUTION_ENABLED=1`).
Без импортированного проверенного пакета запуск остаётся недоступен. Если переменная
не задана, безопасное значение по умолчанию — `0`. Подробности — в [API-контракте](docs/api-contract.md).

- Приложение: http://localhost:5173
- API: http://localhost:8000/api/v1/health/
- Документация API: http://localhost:8000/api/docs/
- Проверки: `make check`; обновление OpenAPI: `make schema`.

Без Make: `python3 -m venv .venv`, `.venv/bin/python -m pip install -r requirements.txt`, `npm --prefix frontend ci`, `.venv/bin/python backend/manage.py migrate`. На Windows используйте `.venv\Scripts\python.exe` вместо `.venv/bin/python`.

## Данные Beeline

Скачайте [выданный ZIP](https://drive.google.com/file/d/1cQUKtE_cm9TVXgzpcFwQYYUpmuFaJHHT/view), затем:

```bash
.venv/bin/python scripts/import_participant_kit.py /путь/к/архиву.zip
.venv/bin/python backend/manage.py import_participant_data --path data/participant-kit
```

Импорт проверяет SHA-256 архива и сохраняет данные в `data/participant-kit`. Данные синтетические; интерфейс отображает агрегаты из CSV. После импорта обновите страницу.

## Все сервисы через Docker

```bash
cp .env.example .env
# Задайте POSTGRES_PASSWORD в .env: случайная строка без спецсимволов URL.
# Например, значение можно получить через: openssl rand -hex 24
docker compose up --build -d
docker compose exec backend python backend/manage.py import_participant_data --path data/participant-kit
```

Перед импортом распакуйте ZIP предыдущей командой. Compose поднимает frontend, backend, PostgreSQL, Redis и worker; отдельный сервис
`migrate` завершается до старта API. Интерфейс доступен на `localhost:5173`.
Пользователь: `docker compose exec backend python backend/manage.py createsuperuser`.
Для публичного размещения нужны HTTPS, собственный `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`,
`DJANGO_ALLOWED_HOSTS` и `DJANGO_CSRF_TRUSTED_ORIGINS`. Порт БД и Redis наружу не открыт.
Docker не установлен на машине интеграции: запуск контейнеров локально не проверен.

Дополнительные проверки:

```bash
make schema
make check
# Отдельная тестовая PostgreSQL БД и доступный Redis; SQLite для этой проверки не подходит.
REDORDA_OFFICIAL_KIT_TEST="$PWD/data/participant-kit" .venv/bin/python -m pytest -q backend/tests/test_public_environment.py
DATABASE_URL=postgresql://USER@localhost/redorda_test make check-integration
make compose-check  # нужен Docker
```

## OpenAI и проверки

Заполните серверный `.env`: `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`. Режим `openai` использует [Responses API](https://developers.openai.com/api/docs/guides/latest-model) и [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs); при ошибке продолжает расчётную стратегию. По умолчанию `Agent()` работает без сетевых вызовов. Доступ к модели зависит от API-проекта.

```bash
.venv/bin/python scripts/check_openai.py
.venv/bin/python scripts/run_official.py local_eval --runs 10
.venv/bin/python scripts/run_official.py make_submission
.venv/bin/python scripts/build_agent.py --submission artifacts/submission.csv
.venv/bin/python scripts/benchmark_agent.py --runs 10 --policies adaptive fixed_100 template
make check
```

CSV сохраняется в `artifacts/submission.csv`, автономный агент — в `artifacts/delivery/`, сравнение политик — в `artifacts/benchmark.json`. Проверка OpenAI сохраняет предложения для replay в `artifacts/openai_hypotheses_replay.json`.

Прогноз движка и результат локальной симуляции — отдельные показатели. Нижний хвост прогноза учитывает неопределённость эффекта, но не весь риск выборки; доходность не гарантируется.

Лимиты среды: бюджет 100 000, 15 000 контактов с пилотами, до 20 пилотов с запрашиваемым размером 10–200 клиентов, 1–10 кампаний до 5 000 клиентов, до 10 минут. [Описание кейса](https://docs.google.com/document/d/1bt_tgnIXnsnGMMjeaqbOY165MXmYQOTllRCDwcKySKI/edit).
