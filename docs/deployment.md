# hack.1ge.kz на Mac mini

Production использует существующие PostgreSQL, Redis и Cloudflare Tunnel машины.
Сервисы launchd: `com.oa.redorda.api` (Gunicorn, loopback 8013),
`com.oa.redorda.web` (Node proxy, loopback 8103), `com.oa.redorda.worker` (Celery).
Cloudflare направляет `hack.1ge.kz` на `http://127.0.0.1:8103`.

Конфигурация с секретами хранится только в игнорируемом `.env.production`
(образец: `infra/macos/production.env.example`). Для PostgreSQL нужна отдельная
роль `redorda`, владеющая базой `redorda`; Redis использует базу 13.
Не копируйте ключи из других проектов. Пакет организаторов импортируется отдельно.

Основной автодеплой: `com.oa.redorda.autodeploy` проверяет `origin/main` каждые
60 секунд и запускает деплой, если commit отличается от `logs/deployed-revision`.
Проверки выполняются на Mac mini перед применением миграций и перезапуском.
Это работает независимо от GitHub Actions: на момент настройки Actions организации
заблокирован из-за billing issue.

Для первичной установки polling-сервиса замените `__ROOT__` в
`infra/macos/com.oa.redorda.autodeploy.plist` абсолютным путём репозитория,
сохраните результат в `~/Library/LaunchAgents/` и выполните
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.oa.redorda.autodeploy.plist`.
Логи polling: `logs/autodeploy.{out,err}.log`.

Дополнительный канал после восстановления Actions: после push в `main`
GitHub Actions выполняет проверки, затем отправляет подписанный
запрос в существующий `https://hook.1ge.kz/github-webhook`. Секрет Actions:
`MACMINI_WEBHOOK_SECRET`. Dispatcher сверяет имя репозитория
`BAITC-Hacks/hack-182682a3-redorda` и вызывает `scripts/deploy-production-macos.sh`.
Actions подтверждает приём запроса; результат установки — в `logs/deploy.log`,
успешно установленный commit — в `logs/deployed-revision`.

Деплой делает fast-forward до `origin/main`, устанавливает зависимости, выполняет
`make check`, сохраняет backup БД в `backups/`, применяет миграции, собирает статику
и перезапускает только сервисы RedOrda. Незакоммиченные изменения останавливают
автодеплой: они не удаляются и не прячутся в stash. При ошибке миграций требуется
проверка журнала и при необходимости восстановление backup; автоматического отката нет.

Ручной повтор: `bash scripts/deploy-production-macos.sh`.
Первичная установка из текущего дерева: `bash scripts/deploy-production-macos.sh --local`.
Для проверки: `curl -fsS https://hack.1ge.kz/api/v1/health/`.
Логи сервисов: `logs/{api,worker,web}.{out,err}.log`.

Публичный proxy разрешает API только GET/HEAD/OPTIONS, пока не реализована
авторизация записи. POST возвращает 403; локальная разработка не ограничена.
Админка Django использует штатную авторизацию. Секреты и исходники proxy не раздаёт.
Расчёты и экспорт остаются недоступными, пока их не реализуют разработчики.
