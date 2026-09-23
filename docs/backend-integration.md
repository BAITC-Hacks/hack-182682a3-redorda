# Backend: отчёт интеграции, 23 сентября 2026

Ниже сохранены результаты исходного объединения backend. Они относятся к указанным
коммитам, а не к каждой последующей версии. Текущее подключение движка описано в
[контракте интеграции](developers/02-agent-integration.md), конфигурация запуска —
в [deployment.md](deployment.md).

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

## Проверки исходного объединения

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

## Текущее подключение

`Agent.act` и backend используют общий `run_campaigns`. Адаптер передаёт сохранённые
параметры запуска, историю выбранного dataset, события и отмену. Factory создаёт
свежую публичную среду; runner и backend проверяют расходы и ограничения перед
сохранением результата. Веб возвращает прогноз и `simulator_result=null`.

Frontend подключён к session login/CSRF, start/cancel, журналу событий, результатам
и CSV. Персонажи пока используют демонстрационную последовательность; рабочие
передачи задач описаны как следующая итерация в [плане команды](developers/00-team-plan.md).

Регрессионные проверки подключения находятся в `backend/tests/test_engine_integration.py`
и `backend/tests/test_participant_pipeline.py`. Вторая использует настоящий публичный
kit и тело Celery-задачи, но подменяет публикацию в очередь. Проверки живого брокера
и отдельного worker находятся в `backend/tests/integration/`; их результаты следует
указывать отдельно от запуска задач внутри тестового процесса.

## Границы приведённых проверок

- **Docker отсутствует** на машине: `docker compose up` и сборка контейнеров локально
  не выполнялись. Шаблон CI содержит отдельную проверку запуска; локальная валидация
  YAML не заменяет работающие контейнеры. GitHub Actions в main отключён отдельным
  коммитом `8f11873`; его удаление сохранено. Шаблон можно вернуть в
  `.github/workflows/ci.yml` после решения причины отключения. Удалённый CI не запускался;
  существующий автодеплой Mac mini по-прежнему выполняет `make check`.
- Сценарий исходного объединения до CSV использовал тестовый runner. Эта таблица
  не подтверждает оценку нынешней стратегии или её запуск через production UI.
  Для текущей сборки отдельно нужны `local_eval`, два процесса `make_submission`
  с seed 42 и проверка автономного агента по [README](../README.md).
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

Перед включением расчётов импортируйте полный пакет и выполните
`.venv/bin/python backend/manage.py check_agent` в окружении сервера. После успешной
проверки задайте `REDORDA_RUN_EXECUTION_ENABLED=1`, перезапустите API и worker
и проверьте один запуск до CSV. Полный порядок — в [deployment.md](deployment.md).
