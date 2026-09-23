# RedOrda · Beeline Campaign Planner

Планирование тарифных маркетинговых кампаний: аудитория → гипотезы → пилоты → выбор кампаний с оценкой прироста выручки и стоимости контактов.

**Стек:** Django 5.2, DRF, React **18.2.0**, TypeScript, PostgreSQL, Redis, Celery. Общий Python-движок для веба и `Agent.act(env)`. OpenAI Responses API, модель по умолчанию `gpt-6-sol`, настраивается через `.env`.

## Три разработчика, три этапа

**Текущая итерация — живая AI-команда.** Все трое работают параллельно в отдельных
worktree: [порядок работы и приёмка](docs/developers/00-team-plan.md),
[целевой контракт задач, событий и команд](docs/team-contract.md).
Основа — существующий production-интерфейс и четыре маскота: дорабатываем их поведение
и подключение к данным, сохраняя опубликованный дизайн и обычный рабочий сценарий.

| Разработчик | Этап 1: основа | Этап 2: сценарий | Этап 3: проверка |
| --- | --- | --- | --- |
| [1. Backend](docs/developers/01-backend.md) | Задачи, артефакты, snapshot | Команды explain/compare/create_plan | Общая интеграция и надёжность |
| [2. AI-агент](docs/developers/02-agent.md) | Роли и настоящие передачи задач | Объяснение и пересчёт по snapshot | Оценка, лимиты, submission |
| [3. Frontend](docs/developers/03-frontend.md) | Два представления одного запуска | Маскоты по событиям и рабочие панели | Взаимодействие, доступность, демо |

[Общий API-контракт](docs/api-contract.md).

## Что уже есть

Интегрированы четыре backend-ветки: проверка и импорт семи CSV, модели и миграции,
Django sessions/CSRF, очередь Celery, идемпотентный старт, отмена, журнал событий,
сохранённые результаты и CSV. PostgreSQL, Redis, Gunicorn, worker и frontend описаны
в Compose с healthchecks. [Шаблон CI](infra/ci/github-actions.yaml) проверяет
PostgreSQL, живую очередь и сборку Compose. Действующий workflow удалён параллельным
коммитом main; его удаление сохранено. Автодеплой Mac mini продолжает запускать `make check`.

**AI-движок:** общий runner и `Agent.act`, анализ истории, адаптивные пилоты, оценки неопределённости, выбор каналов, проверка лимитов, события и отмена, GPT-6 с fallback/replay, воспроизводимый CSV и сборка автономного агента. [Контракт runner и подключение backend](docs/developers/02-agent-integration.md).

По умолчанию `Agent()` использует `strategy=baseline` и политику пилотов `adaptive`.
После пилотов ограниченный поиск уточняет финальный портфель на фиксированных
оценках эффекта, не проводя дополнительных пилотов. По умолчанию он проверяет до
256 вариантов, с ограничением времени 10 секунд. Он использует ту же функцию
оценки и проверку лимитов, что и исходный портфель; улучшение прогноза не означает
гарантированного улучшения результата симуляции.
Ограничение времени кооперативное: начатая оценка завершается. При остановке
по `time_limit` результат может зависеть от скорости машины; при завершении по
лимиту вариантов одинаковые данные и seed дают воспроизводимый результат.
[Алгоритм, протокол сравнения и границы поиска](docs/developers/02-portfolio-search.md).

**Интеграция в коде:** backend подключён к общему runner через публичную среду,
передаёт настройки, сохраняет события, наблюдения и прогнозы. Frontend использует
login/CSRF, start/cancel, события, результаты и CSV; добавлены четыре пиксельных маскота.
Доступность расчётов на сервере определяется конфигурацией и readiness-проверками.

**Следующая задача:** связать персонажей с настоящими задачами/артефактами,
добавить объяснение, сравнение и изменённые планы, сохранить два режима одного запуска.
Сейчас анимация персонажей использует демонстрационную последовательность по таймеру.
Прогноз и результат симулятора различаются: текущий web-пайплайн возвращает
`simulator_result=null`. Production-сценарий и конкурсный результат проверяются отдельно.
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

Расчёты включаются через `REDORDA_RUN_EXECUTION_ENABLED=1` и настроенный
`REDORDA_ENVIRONMENT_FACTORY`; подробный протокол включения и входа —
в [API-контракте](docs/api-contract.md).

- Приложение: http://localhost:5173
- API: http://localhost:8000/api/v1/health/
- Документация API: http://localhost:8000/api/docs/
- Проверки: `make check`; обновление OpenAPI: `make schema`.

Проверки импорта отдельно: `.venv/bin/python -m pytest -q backend/tests/test_dataset_upload.py`.
Они покрывают демонабор, multipart-загрузку, лимиты, ошибки CSV, сохранение файлов,
повторный выбор набора, доступ с CSRF и схему API. `make check` также проверяет
совместимость с прежним семифайловым CLI-импортом и собирает frontend.

Без Make: `python3 -m venv .venv`, `.venv/bin/python -m pip install -r requirements.txt`, `npm --prefix frontend ci`, `.venv/bin/python backend/manage.py migrate`. На Windows используйте `.venv\Scripts\python.exe` вместо `.venv/bin/python`.

## Данные Beeline

Откройте **Аудитория** и выберите **Импортировать демоданные**: четыре исходных
CSV уже включены в `data/demo/`, скачивать пакет для этого сценария не нужно.
Для своих данных выберите или перетащите `dict_tariff.csv`, `traffic.csv`,
`arpu_monthly.csv` и `change_tariff.csv` в том же формате. Можно добавлять файлы
по одному. Лимит — 30 МиБ на файл и 50 МиБ на набор. Интерфейс показывает передачу,
проверку на сервере и анимацию взлёта после успешного импорта; сводка обновляется сразу.

Четыре CSV дают фактическое число абонентов, тарифов и строк. Прогноз ARPU и готовые
сегменты доступны только в полном пакете. Пользовательские загрузки хранятся в
`backend/media/` (в Docker — общий том `dataset_media`) и не входят в Git.
Ошибки не меняют текущие данные, а повторный импорт не создаёт дубликат.

Четыре CSV доступны для просмотра и создания черновиков. Для запуска симулятора
нужен готовый профиль аудитории: интерфейс объясняет это ограничение и не запускает
неполный набор. Для полного пакета с профилем и словарём признаков сохранён CLI-импорт.
Скачайте [выданный ZIP](https://drive.google.com/file/d/1cQUKtE_cm9TVXgzpcFwQYYUpmuFaJHHT/view), затем:

```bash
.venv/bin/python scripts/import_participant_kit.py /путь/к/архиву.zip
.venv/bin/python backend/manage.py import_participant_data --path data/participant-kit
```

Архив проверяется по SHA-256, файлы организаторов сохраняются без изменений в игнорируемую Git папку. Данные синтетические. Импорт повторяемый: в БД сохраняются отдельные профили абонентов и агрегированная сводка; интерфейс показывает агрегаты из CSV, не зашитые числа. Обновите страницу после импорта.

Для команд `scripts/run_official.py` незаданный или пустой `PARTICIPANT_KIT_DIR`
означает `data/participant-kit` относительно репозитория. Можно указать другой
каталог; относительный путь также считается от корня репозитория.

## Все сервисы через Docker

```bash
cp .env.example .env
# Задайте POSTGRES_PASSWORD в .env: случайная строка без спецсимволов URL.
# Например, значение можно получить через: openssl rand -hex 24
docker compose up --build -d
docker compose exec backend python backend/manage.py import_participant_data --path data/participant-kit
```

Для CLI-импорта распакуйте ZIP предыдущей командой; демоимпорт из интерфейса работает без ZIP. Compose поднимает frontend, backend, PostgreSQL, Redis и worker; отдельный сервис
`migrate` завершается до старта API. Интерфейс доступен на `localhost:5173`.
Пользователь: `docker compose exec backend python backend/manage.py createsuperuser`.
Для публичного размещения нужны HTTPS, собственный `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`,
`DJANGO_ALLOWED_HOSTS` и `DJANGO_CSRF_TRUSTED_ORIGINS`. Порт БД и Redis наружу не открыт.
Полная сборка Compose проверяется отдельно от тестов очереди на PostgreSQL/Redis.

Дополнительные проверки:

```bash
make schema
make check
# Необязательный сквозной тест на импортированном официальном пакете:
REDORDA_OFFICIAL_KIT_TEST="$PWD/data/participant-kit" .venv/bin/python -m pytest -q backend/tests/test_public_environment.py
# Отдельная тестовая PostgreSQL БД и доступный Redis; SQLite для этой проверки не подходит.
DATABASE_URL=postgresql://USER@localhost/redorda_test make check-integration
make compose-check  # нужен Docker
```

## OpenAI и проверки

Режим `openai` использует [Responses API](https://developers.openai.com/api/docs/guides/latest-model) и [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs); при ошибке продолжает расчётную стратегию. Для него заполните серверный `.env`: `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`. Модель по умолчанию — `gpt-6-sol`. `Agent()` и проверки ниже работают без сетевых вызовов.

```bash
.venv/bin/python scripts/run_official.py local_eval --runs 10
.venv/bin/python scripts/run_official.py make_submission
cp artifacts/submission.csv artifacts/submission-seed42-first.csv
.venv/bin/python scripts/run_official.py make_submission
cmp artifacts/submission-seed42-first.csv artifacts/submission.csv
.venv/bin/python scripts/build_agent.py --submission artifacts/submission.csv
.venv/bin/python scripts/benchmark_agent.py --runs 10 --policies adaptive fixed_100 template
.venv/bin/python scripts/compare_portfolio_search.py --seeds 0 1 2 3 4 5 6 7 8 9
make check
```

CSV сохраняется в `artifacts/submission.csv`, автономный агент — в `artifacts/delivery/`, сравнение политик — в `artifacts/benchmark.json`. Два вызова `make_submission` выше запускают отдельные процессы с seed 42; `cmp` проверяет равенство файлов.

`compare_portfolio_search.py` сравнивает исходный и уточнённый портфели на одних
пилотах. Seed 0–9 — разработческая выборка, а не независимый закрытый тест;
результат не подтверждает преимущество на новых данных.

Для проверки автономного агента используйте отдельную копию полного participant kit
и новое виртуальное окружение по `artifacts/delivery/README.md`. Установите только
зависимости сборки и сравните полученный `submission.csv` с исходным. Manifest
фиксирует SHA-256 модулей: после изменения движка сборку и проверки нужно повторить.

Отдельная сетевая проверка `.venv/bin/python scripts/check_openai.py` проверяет доступ
API-проекта к модели и сохраняет предложения для replay в
`artifacts/openai_hypotheses_replay.json`.

Прогноз движка и результат локальной симуляции — отдельные показатели. Нижний хвост прогноза учитывает неопределённость эффекта, но не весь риск выборки; доходность не гарантируется.

Лимиты среды: бюджет 100 000, 15 000 контактов с пилотами, до 20 пилотов с запрашиваемым размером 10–200 клиентов, 1–10 кампаний до 5 000 клиентов, до 10 минут. [Описание кейса](https://docs.google.com/document/d/1bt_tgnIXnsnGMMjeaqbOY165MXmYQOTllRCDwcKySKI/edit).
