# RedOrda · Beeline Campaign Planner

Веб-приложение для планирования тарифных маркетинговых кампаний: аудитория → гипотезы → пилоты → план с максимальным приростом выручки за вычетом стоимости контактов.

**Стек:** Django 5.2, DRF, React **18.2.0**, TypeScript, PostgreSQL, Redis, Celery. Общий Python-движок для веба и судейского `Agent.act(env)`. OpenAI Responses API, модель по умолчанию `gpt-6-sol`, настраивается через `.env`.

## Три разработчика, три этапа

| Разработчик | Этап 1: основа | Этап 2: сценарий | Этап 3: сдача |
| --- | --- | --- | --- |
| [1. Backend](docs/developers/01-backend.md) | Данные, модели, API | Celery, прогресс, результаты | Интеграция, ошибки, Docker |
| [2. AI-агент](docs/developers/02-agent.md) | Базовая стратегия и пилоты | GPT-6, разведка, оптимизация | Оценка, лимиты, submission |
| [3. Frontend](docs/developers/03-frontend.md) | Экраны и API-клиент | Запуск, прогресс, таблица | Адаптация, проверка, демо |

В каждом ТЗ есть инструкция для ИИ-помощника. [Общий API-контракт](docs/api-contract.md).

## Что уже есть

Интегрированы четыре backend-ветки: проверка и импорт семи CSV, модели и миграции,
Django sessions/CSRF, очередь Celery, идемпотентный старт, отмена, журнал событий,
сохранённые результаты и CSV. PostgreSQL, Redis, Gunicorn, worker и frontend описаны
в Compose с healthchecks. [Шаблон CI](infra/ci/github-actions.yaml) проверяет
PostgreSQL, живую очередь и сборку Compose. Действующий workflow удалён параллельным
коммитом main; его удаление сохранено. Автодеплой Mac mini продолжает запускать `make check`.

**Блокеры:** `Agent.act` и адаптер публичной среды ещё не реализованы; запуск выключен
(`engine_unavailable`). Полный успешный сценарий проверяется только тестовым runner.
Frontend ещё должен подключить login/CSRF и новые API; при публичном режиме данные
доступны после входа. Стратегия, реальные расчёты и конкурсный результат не объявлены готовыми.
[Отчёт интеграции и проверки](docs/backend-integration.md).
[Публичный сайт](https://hack.1ge.kz), [автодеплой на Mac mini](docs/deployment.md).

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

`REDORDA_RUN_EXECUTION_ENABLED=0` сохраняется до готовности общего агента и factory;
подробный протокол включения и входа — в [API-контракте](docs/api-contract.md).

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

Архив проверяется по SHA-256, файлы организаторов сохраняются без изменений в игнорируемую Git папку. Данные синтетические. Импорт повторяемый; интерфейс показывает агрегаты из CSV, не зашитые числа. Обновите страницу после импорта.

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
DATABASE_URL=postgresql://USER@localhost/redorda_test make check-integration
make compose-check  # нужен Docker
```

## OpenAI и проверка организаторов

Заполните только серверный `.env`: `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`. Доступ к модели зависит от API-проекта. Каркас использует [Responses API и GPT-6](https://developers.openai.com/api/docs/guides/latest-model) и [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs); при ошибке выдаёт сигнал для расчётного fallback. Интеграция в стратегию — задача разработчика 2.

После реализации `Agent.act`:

```bash
.venv/bin/python scripts/run_official.py local_eval --runs 10
.venv/bin/python scripts/run_official.py make_submission
```

CSV появится в `artifacts/submission.csv`. Сейчас судейский агент явно помечен как нереализованный; скелет не является готовой конкурсной стратегией.

Лимиты: 100 000 у. е., 15 000 контактов с пилотами, 20 пилотов по 10–200 клиентов, 1–10 кампаний до 5 000 клиентов, до 10 минут. В комментарии шаблона организаторов указан более строгий ориентир 5 минут; целимся в него, руководство участника допускает 10 минут. [Официальное ТЗ](https://docs.google.com/document/d/1bt_tgnIXnsnGMMjeaqbOY165MXmYQOTllRCDwcKySKI/edit).
