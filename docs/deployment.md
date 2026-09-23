# Развёртывание RedOrda

Frontend обращается к `/api/v1/` на том же домене. Ключ OpenAI задаётся на сервере:
в `.env.production` для Mac mini или в `.env` для Compose. Его используют API и
Celery worker; frontend ключ не получает. Модель остаётся `gpt-6-sol`.

## Mac mini: hack.1ge.kz

Сервисы launchd: `com.oa.redorda.api` (Gunicorn, loopback 8013),
`com.oa.redorda.web` (Node proxy, loopback 8103), `com.oa.redorda.worker` (Celery).
Cloudflare Tunnel направляет `hack.1ge.kz` на `http://127.0.0.1:8103`.
PostgreSQL использует отдельную роль и базу `redorda`; Redis — базу 13.

Заполните серверный `.env.production` по образцу
[`infra/macos/production.env.example`](../infra/macos/production.env.example).
Укажите действительные `DATABASE_URL`, `DJANGO_SECRET_KEY`, домен в
`DJANGO_ALLOWED_HOSTS` и HTTPS-origin в `DJANGO_CSRF_TRUSTED_ORIGINS`. Настройки агента:

```dotenv
OPENAI_API_KEY=<server-api-key>
OPENAI_MODEL=gpt-6-sol
OPENAI_REASONING_EFFORT=low
OPENAI_TIMEOUT_SECONDS=30
PARTICIPANT_KIT_DIR=/ABSOLUTE/REPOSITORY/data/participant-kit
REDORDA_ENVIRONMENT_FACTORY=apps.campaigns.services.participant_environment.create_environment
REDORDA_RUN_EXECUTION_ENABLED=0
```

Путь должен быть доступен API и worker. Для веб-запуска источником служит
`Dataset.source_dir`, сохранённый при импорте. `PARTICIPANT_KIT_DIR` задаёт путь
для отдельных CLI-запусков; пустое значение использует `data/participant-kit`
в текущем репозитории. При заданном значении используйте абсолютный путь.

После размещения кода выполните на сервере:

```bash
cd /ABSOLUTE/REPOSITORY
bash scripts/deploy-production-macos.sh
.venv/bin/python scripts/import_participant_kit.py /ABSOLUTE/participant-kit.zip \
  --destination /ABSOLUTE/REPOSITORY/data/participant-kit

(
  set -a
  source .env.production
  set +a
  .venv/bin/python backend/manage.py import_participant_data --path "$PARTICIPANT_KIT_DIR"
  .venv/bin/python backend/manage.py check_agent
  .venv/bin/celery --workdir backend -A config inspect ping --timeout 3
)
```

`check_agent` проверяет последний импортированный dataset, импорт factory, профиль,
справочники и исходные лимиты новой среды. Пилоты и запросы к модели не выполняются.
Команда показывает только наличие ключа и имя модели; доступ к API-проекту этим
не проверяется. Доступность брокера и worker проверяется отдельно.

После успешной проверки установите `REDORDA_RUN_EXECUTION_ENABLED=1` в серверном
`.env.production` и перезапустите API и worker, чтобы они перечитали конфигурацию:

```bash
launchctl kickstart -k "gui/$(id -u)/com.oa.redorda.api"
launchctl kickstart -k "gui/$(id -u)/com.oa.redorda.worker"
curl -fsS https://hack.1ge.kz/api/v1/ready/
```

При `DJANGO_DEBUG=0` работа с данными требует Django-сессии. Создать пользователя
можно командой `.venv/bin/python backend/manage.py createsuperuser` внутри такого же
блока с загруженным `.env.production`. После входа проверьте один запуск через UI,
появление событий, итоговых кампаний и CSV. Режим `baseline` работает без OpenAI;
для режима `openai` ключ должен быть настроен у worker.

Worker работает с `prefork` и `concurrency=1`. Для API и worker должны совпадать
`DATABASE_URL`, `REDIS_URL`, `REDORDA_QUEUE=redorda` и `REDORDA_REDIS_PREFIX=redorda:`.
Для распределённого ограничения входа используется `CACHE_URL`.

## Compose

Compose загружает `.env` в backend и worker. Пакет данных монтируется в оба сервиса
по одному пути `/workspace/data/participant-kit`, только для чтения.

```bash
cp .env.example .env
```

Задайте в `.env` `POSTGRES_PASSWORD`, серверный `OPENAI_API_KEY` при использовании
OpenAI и `PARTICIPANT_KIT_DIR=/workspace/data/participant-kit`. Оставьте
`REDORDA_RUN_EXECUTION_ENABLED=0` на время проверки; путь factory уже указан в образце.
Затем:

```bash
.venv/bin/python scripts/import_participant_kit.py /ABSOLUTE/participant-kit.zip

docker compose up --build -d
docker compose exec backend python backend/manage.py import_participant_data \
  --path /workspace/data/participant-kit
docker compose exec backend python backend/manage.py check_agent
docker compose exec worker python backend/manage.py check_agent
docker compose exec worker celery --workdir backend -A config inspect ping --timeout 3
```

После успешных проверок задайте `REDORDA_RUN_EXECUTION_ENABLED=1` в `.env` и
пересоздайте сервисы: обычный `restart` не обновляет переменные контейнера.

```bash
docker compose up -d --force-recreate backend worker
curl -fsS http://127.0.0.1:5173/api/v1/ready/
```

При необходимости создайте пользователя:
`docker compose exec backend python backend/manage.py createsuperuser`.
`migrate` применяет миграции и собирает статику до старта API; импорт dataset выполняется
явно. При переносе существующей БД убедитесь, что сохранённый `Dataset.source_dir`
доступен обоим контейнерам: повторный валидный импорт одинакового checksum обновляет
только `source_dir` существующего Dataset, сохраняя его ID и связанные запуски.

## Проверки и обновления

`/api/v1/health/` проверяет соединение с БД; `/api/v1/ready/` — БД и Redis.
Они не заменяют `check_agent` и `celery inspect ping`.
Логи Mac: `logs/{api,worker,web}.{out,err}.log`; журнал установки — `logs/deploy.log`.

Автодеплой Mac mini (`com.oa.redorda.autodeploy`) проверяет `origin/main` каждые
60 секунд и сравнивает commit с `logs/deployed-revision`. Он делает fast-forward,
устанавливает зависимости, выполняет `make check`, сохраняет backup БД в `backups/`,
применяет миграции, собирает frontend/статику и перезапускает сервисы. Изменённое
рабочее дерево останавливает обновление. Конфигурация `.env.production` автоматически
не редактируется; после её изменения нужен перезапуск API и worker.

Ручной повтор: `bash scripts/deploy-production-macos.sh`. Первичная установка из
подготовленного рабочего дерева: `bash scripts/deploy-production-macos.sh --local`.
Сам deploy-скрипт проверяет HTTP health и frontend; после обновления адаптера или
данных дополнительно выполните проверки агента и worker выше.

Шаблон GitHub Actions находится в `infra/ci/github-actions.yaml`; активного workflow
в `.github/workflows` нет. Автодеплой polling работает независимо от этого шаблона.
Для первичной установки polling-сервиса замените `__ROOT__` в
`infra/macos/com.oa.redorda.autodeploy.plist` абсолютным путём репозитория, сохраните
файл в `~/Library/LaunchAgents/` и выполните:

```bash
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.oa.redorda.autodeploy.plist"
```
