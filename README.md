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

Скелет запускается: импорт сводки публичного датасета, обзор аудитории, создание и просмотр черновиков планов, API и OpenAPI, миграция БД, адаптивный React-интерфейс, каркас OpenAI с типизированным ответом, тесты и CI.

**Ещё предстоит:** стратегия `Agent.act`, запуск расчётов в Celery, пилоты, результаты и экспорт. Кнопка запуска пока недоступна. Вызовы OpenAI не выполняются автоматически. Публичный сайт: https://hack.1ge.kz — API пока доступен только для чтения. [Автодеплой на Mac mini](docs/deployment.md).

## Локальный запуск

Python 3.11+ и Node.js 20.19+ (рекомендуются Python 3.12 и Node.js 22).

```bash
cp .env.example .env
make setup
make migrate
```

В двух терминалах: `make backend` и `make frontend`.

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

Архив проверяется по SHA-256, файлы организаторов сохраняются без изменений в игнорируемую Git папку. Данные синтетические. Импорт повторяемый: в БД сохраняются отдельные профили абонентов и агрегированная сводка; интерфейс показывает агрегаты из CSV, не зашитые числа. Обновите страницу после импорта.

## Все сервисы через Docker

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec backend python backend/manage.py import_participant_data --path data/participant-kit
```

Перед импортом распакуйте ZIP предыдущей командой. Compose поднимает frontend, backend, PostgreSQL, Redis и worker; интерфейс также доступен на `localhost:5173`. Worker пока подготовлен для подключения расчётов на этапе 2. Compose предназначен для разработки.

## OpenAI и проверка организаторов

Заполните только серверный `.env`: `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`. Доступ к модели зависит от API-проекта. Каркас использует [Responses API и GPT-6](https://developers.openai.com/api/docs/guides/latest-model) и [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs); при ошибке выдаёт сигнал для расчётного fallback. Интеграция в стратегию — задача разработчика 2.

После реализации `Agent.act`:

```bash
.venv/bin/python scripts/run_official.py local_eval --runs 10
.venv/bin/python scripts/run_official.py make_submission
```

CSV появится в `artifacts/submission.csv`. Сейчас судейский агент явно помечен как нереализованный; скелет не является готовой конкурсной стратегией.

Лимиты: 100 000 у. е., 15 000 контактов с пилотами, 20 пилотов по 10–200 клиентов, 1–10 кампаний до 5 000 клиентов, до 10 минут. В комментарии шаблона организаторов указан более строгий ориентир 5 минут; целимся в него, руководство участника допускает 10 минут. [Официальное ТЗ](https://docs.google.com/document/d/1bt_tgnIXnsnGMMjeaqbOY165MXmYQOTllRCDwcKySKI/edit).
