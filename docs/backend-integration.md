# Backend: интеграция живой команды, 23 сентября 2026

Поверх `origin/main` (`f66fbfc`) обычными merge объединены:

| Ветка | Commit |
| --- | --- |
| `codex/backend-team-state` | `46a0424` |
| `codex/backend-team-commands` | `e05ee08` |
| `codex/backend-team-api` | `be7121d` |

Сохранены импорт CSV, Subscriber, текущий интерфейс и настройки автодеплоя из main.
Добавлена merge-миграция двух ветвей `0003`. Исправлены несовместимости UUID/404,
нормализация запросов, сохранение задач команд и атомарное завершение с артефактом.
Поздний ответ после timeout не публикуется. Обновлены OpenAPI, примеры Swagger,
TypeScript-типы и методы клиента; дизайн интерфейса не менялся.

## Проверки этой интеграции

- `make check`: Ruff, Django check, отсутствие новых миграций;
  **454 Python-теста passed, 17 skipped** (отдельные PostgreSQL/live-проверки).
  Frontend: **37 Vitest + 9 Node-тестов**, TypeScript и Vite build прошли.
- `make schema`: валидация OpenAPI без предупреждений.
- Миграции применены к отдельной PostgreSQL-базе `redorda_team_integration`.
- `DATABASE_URL=postgresql://ai@localhost:5432/redorda_team_integration make check-integration`:
  **17 passed**, без пропусков. Реальные Redis/Celery, отдельный prefork worker,
  конкурентные start/submit/claim, snapshot с согласованным курсором, гонка timeout
  с публикацией артефакта, HTTP → snapshot → команда → связанный черновик.
- Прицельные проверки команд и конкуренции на PostgreSQL: **22 passed**.
- В `make check` включены проверки настоящего общего runner с контролируемой
  публичной средой: лимиты, сохранение прогноза, отмена и CSV. Транспортные тесты
  старого pipeline используют runner только из tests; конкурсный score этим не измеряется.
- Docker отсутствует: Compose запуск/сборка в этой интеграции не выполнялись.

## Осталось подключить

- AI-движок: события задач ролей, `explain(snapshot, campaign_id)` и
  `compare(snapshot, constraints)`, поддержка `allowed_channels`. Сейчас эти AI-команды
  скрыты из доступных; прямой запрос завершается `capability_unavailable`.
- Frontend: подключение существующих персонажей к `team/` и событиям, панели артефактов
  и команды. API и клиентские методы готовы. Новый план с бюджетом создаётся как draft,
  без копирования пилотов и без автоматического старта.
- Публичный автодеплой проверяется отдельно от локальных тестов и Git push.

---

# Исторический отчёт первоначальной backend-интеграции

Ниже зафиксировано состояние ранней интеграции. Его старые блокеры AI/frontend
не описывают текущее состояние; актуальные ограничения перечислены выше.

Ветка: `codex/backend-integration`, отдельный worktree. Ветки объединены обычными
merge в порядке: данные → выполнение → результаты → HTTP API.

| Ветка | Включённый commit |
| --- | --- |
| `codex/backend-data` | `d85bd38` |
| `codex/backend-execution` | `51a6993` |
| `codex/backend-results` | `866d328` |
| `codex/backend-api` | `0e3fbe2` |

Сохранены изменения frontend и Mac mini deployment из `origin/main` (`b28ccff`),
затем бренд Janymda и удаление GitHub Actions (`8f11873`), пришедшие перед push.
Конфликт был в docstring `services/__init__.py`; модели не дублировались.

## Что интеграция добавила

- Django session login/logout/me и CSRF endpoint; обязательный вход в публичном режиме.
- Gunicorn, отдельная миграция/collectstatic в Compose, healthchecks API/Redis/worker/frontend,
  непривилегированный backend-контейнер, общая Django static директория.
- Очередь и Redis prefix RedOrda, prefork worker, совместимость Celery spawn на macOS.
- Запрет запуска неготового агента: API 503 `engine_unavailable`, без сообщения в очереди.
- Проверки лимитов перед пилотом и до completed; известные расходы учитываются даже
  при отсутствующих метриках остальных кампаний.
- PostgreSQL/Redis и Compose jobs в шаблоне CI, обновлённые OpenAPI и auth-контракт.
  Параллельное удаление активного workflow сохранено: шаблон лежит в
  `infra/ci/github-actions.yaml` и не запускается автоматически.

## Реально выполненные проверки

Среда: macOS arm64, Python 3.11.9, PostgreSQL 15.13, Redis, Node.js 22.15.1.

| Проверка | Результат |
| --- | --- |
| `DATABASE_URL=postgresql://ai@localhost:5432/redorda_integration_20260923 REDORDA_LIVE_TESTS=1 make check` | **107 passed**, без пропусков; Ruff, Django check, отсутствие новых миграций, React/TypeScript production build — успешно |
| `make schema`, повторная генерация `spectacular --validate --fail-on-warn` и diff | Успешно, схема воспроизводится без предупреждений |
| Восемь одновременных HTTP start с одинаковыми и различными ключами на PostgreSQL | Одна публикация; для различных ключей 1×202 и 7×409 |
| Восемь одновременных worker claims на PostgreSQL | Только один получает запуск |
| Реальные Redis/Celery + runner исключительно из tests | Импорт → черновик → старт → события → результат → два одинаковых CSV; один пилот |
| Повторный старт, отмена между пилотами, исключение worker, ошибка таймаута | Пройдены; повторных пилотов нет; незавершённый результат не экспортируется |
| Реальное соединение с недоступным Redis | API 503, failed/queue_unavailable; поздний claim не запускает расчёт |
| Нереализованный Agent.act / отсутствующий factory | engine_unavailable; результатов нет |
| Отдельный реальный prefork worker через subprocess | Принимает задачу и сохраняет failed/engine_unavailable; regression test включён |
| `OPENAI_API_KEY` отсутствует | Контролируемый PlanningUnavailable без сетевого запроса; тест общего gateway |
| Проверки превышения бюджета/контактов/пилотов/числа кампаний | Пройдены, включая неизвестную стоимость части кампаний |
| Импорт официального пакета в отдельную PostgreSQL БД | **23 441** клиентов, все миграции применены |
| Отдельные Gunicorn + Node proxy + prefork worker | HTTP frontend 200, readiness 200, анонимные данные 403, Secure CSRF cookie; worker ping и задача проверены |
| Compose YAML по официальной JSON Schema Compose | Валиден; YAML CI также разобран |
| `node --check`, `bash -n`, `pip check`, `git diff --check` | Успешно |

Live-тесты используют собственные очереди и Redis prefixes; общая БД Redis не очищается.
Пароли тестовых пользователей генерируются во время теста. Пакет организаторов,
`.env`, секреты, БД, результаты и node_modules не входят в коммиты.

## Что не проверено / зависит от команды

- **Docker отсутствует** на машине: `docker compose up` и сборка контейнеров локально
  не выполнялись. Шаблон CI содержит отдельную проверку запуска; локальная валидация
  YAML не заменяет работающие контейнеры. GitHub Actions в main отключён отдельным
  коммитом `8f11873`; его удаление сохранено. Шаблон можно вернуть в
  `.github/workflows/ci.yml` после решения причины отключения. Удалённый CI не запускался;
  существующий автодеплой Mac mini по-прежнему выполняет `make check`.
- **AI:** Agent.act и factory публичной среды не готовы. Запуск оставлен выключенным.
  Сценарий до CSV проверяет backend с тестовым runner, а не настоящую стратегию.
  Официальный local_eval/submission, реальные пилоты, прогноз и эффект не проверены.
- Factory должен принимать EngineContext, соблюдать общий бюджет/контакты/пилоты,
  возвращать реальные ответы среды. Расходы финальных кампаний проверяются backend
  по доступным метрикам; при неизвестных значениях нужен контроль в AI-адаптере.
- **Frontend:** подключить session login, смену CSRF после входа, заголовки POST,
  обработку 401/403, события/results/CSV и обновить типы по OpenAPI. Контракт передан
  в `docs/api-contract.md`; подтверждения frontend-разработчика пока нет.
- Обработчик timeout проверен тестовым исключением; ожидание 600 секунд и принудительное
  убийство занятого worker в этом прогоне не выполнялись. Guard сработает после
  восстановления worker, если все worker были недоступны.
- Публичный HTTPS-деплой не приравнивается к локальному smoke-тесту. Mac mini подхватит
  main своим существующим механизмом; его состояние следует проверять отдельно.

## Запуск

Локально: `cp .env.example .env`, `make setup`; задать PostgreSQL/Redis URL,
`make migrate`, импортировать пакет, создать пользователя через `manage.py createsuperuser`.
Затем в отдельных терминалах: `make backend`, `make frontend`, `make worker`.

Docker: задать случайный `POSTGRES_PASSWORD` в `.env`, `docker compose up --build -d`;
импорт и createsuperuser выполняются через `docker compose exec backend ...`.
Пользователь вводит пароль вручную. Подробные команды — в README.

`REDORDA_RUN_EXECUTION_ENABLED=0` сохранять до готовности Agent.act и
`REDORDA_ENVIRONMENT_FACTORY`. Полного рабочего решения конкурса этот merge не заявляет.
