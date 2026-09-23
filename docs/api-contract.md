# Общий API-контракт RedOrda

React → `/api/v1/` → Django 5.2 → Celery → общий `campaign_engine` → публичная среда Beeline.
Источник машинных схем: [openapi.yaml](openapi.yaml); подробные ответы: [backend-api.md](backend-api.md).

## Доступ и договорённость для frontend

При `DJANGO_DEBUG=0` API всегда требует активного пользователя, независимо от
`REDORDA_REQUIRE_AUTH`. В локальной разработке включить это поведение можно через
`REDORDA_REQUIRE_AUTH=1`. Пользователи работают в общей командной рабочей области;
разделения планов по владельцам пока нет. Пользователь создаётся администратором
через `manage.py createsuperuser`; публичной регистрации и встроенных паролей нет.

Frontend использует один origin, `credentials: 'include'` и следующий порядок:

1. `GET auth/me/` → `{authenticated: false, user: null}` либо данные текущего пользователя.
2. `GET auth/csrf/` → `{csrf_token: string}`; ответ также устанавливает cookie `csrftoken`.
3. `POST auth/login/` с JSON `{username, password}` и заголовком `X-CSRFToken`.
   Ответ 200: `{authenticated: true, user: {id, username, is_staff}, csrf_token}`.
   После входа токен меняется — дальнейшие запросы используют **токен из ответа login**.
4. `POST auth/logout/` с действующим токеном → 200, анонимная сессия и новый токен.

`sessionid` — HttpOnly cookie; в production обе cookie Secure, SameSite=Lax.
Для всех POST нужен `X-CSRFToken`. Неверный пароль/неактивный пользователь: 401
`invalid_credentials`; отсутствие сессии: 403 `not_authenticated`; ошибка CSRF:
403 `csrf_failed` на login/logout либо `permission_denied` на остальных POST.
Ограничение login: 10 попыток/минуту на IP, затем 429 `throttled`.
При 401/403 frontend останавливает polling и показывает вход; CSRF обновляется через
`auth/csrf/`. Пароль и session cookie не сохраняются в localStorage.

`health/`, `ready/`, auth discovery, `/api/schema/` и `/api/docs/` доступны без входа.
Данные и операции API требуют сессии в публичном режиме. Контракт подготовлен для frontend; его
подключение и обновление TypeScript-типов остаются задачей frontend-разработчика.
Файлы frontend при этой интеграции не изменялись.

## Маршруты

| Метод | Путь относительно `/api/v1/` | Результат |
| --- | --- | --- |
| GET | `health/` | Доступность Django и БД |
| GET | `ready/` | 200: `{status: "ok", database: true, broker: true}`; 503 при сбое БД/Redis |
| GET | `meta/` | Лимиты, стоимость каналов, флаги функций |
| GET | `datasets/current/` | Импортированная сводка; 404 при отсутствии |
| POST | `datasets/import-demo/` | Импорт четырёх встроенных CSV; 201 новый / 200 повторный |
| POST | `datasets/import/` | Загрузка четырёх CSV через multipart `files`; 201 новый / 200 повторный |
| GET / POST | `runs/` | История / сохранение черновика; без датасета 409 `dataset_required` |
| GET | `runs/{id}/` | Состояние и параметры плана |
| POST | `runs/{id}/start/` | 202; обязателен `Idempotency-Key` длиной 1–255 |
| POST | `runs/{id}/cancel/` | 200; draft → 409; queued отменяется сразу, running — между шагами |
| GET | `runs/{id}/events/?after=0&limit=100` | `{results, next_after, has_more}`, limit 1–200 |
| GET | `runs/{id}/results/` | Сохранённые кампании, totals, warnings; до completed — 409 |
| GET | `runs/{id}/export/` | Стабильный UTF-8 CSV из БД; до completed — 409 |

POST `runs/`: `name`, `budget` (decimal-строка), `max_contacts`, `max_pilots`, `seed`,
`strategy="baseline"`. Датасет выбирает сервер. Неизвестные поля запрещены.
Списки планов: `{count, next, previous, results}`. Ошибки: `{error: {code, message, fields}}`.
ID — UUID; время — ISO 8601; деньги — decimal-строки. События имеют возрастающий
числовой ID; клиент сохраняет `next_after` даже после пустой страницы.

## Импорт из интерфейса

`POST datasets/import-demo/` принимает пустое тело или `{}` и использует файлы
из `data/demo/`. `POST datasets/import/` принимает `multipart/form-data` с четырьмя
повторениями поля `files`: `dict_tariff.csv`, `traffic.csv`, `arpu_monthly.csv`,
`change_tariff.csv`. Имена должны совпадать точно. Кодировка UTF-8 (BOM допустим),
разделитель — запятая, заголовки и столбцы — как в демонаборе. Максимум 30 МиБ
на файл и 50 МиБ на весь набор. Неизвестные поля, лишние, повторные, пустые и
недостающие файлы отклоняются. Сервер проверяет структуру строк, числовые значения
и ссылки на тарифы до смены текущего набора.

Оба метода возвращают объект `Dataset`, как `datasets/current/`. Повтор тех же
байтов использует существующий набор и делает его текущим; исходная отметка
происхождения сохраняется. Старые планы продолжают ссылаться на свои наборы.
Неудачный импорт не заменяет текущий набор. Загрузка сохраняется в закрытом
серверном хранилище `MEDIA_ROOT/datasets/`; путь не входит в ответ API.

У четырёхфайлового набора `summary.format="raw_csv"`, `source_kind="demo"|"upload"`,
`file_rows` содержит число строк по именам файлов, `tariff_count` — размер
справочника. `customer_count` — уникальные `ID_NUMBER` в `traffic.csv`.
`baseline_arpu=null`, три словаря `segments` пусты: готового прогноза и сегментов
в этих таблицах нет. `synthetic=true` для встроенного демо, `null` для загрузки
неизвестного происхождения. Семифайловый CLI-импорт сохраняет прежний формат сводки.

Фронтенд передаёт CSRF-токен в обоих POST. Прогресс передачи файлов берётся из
XHR; проверка CSV на сервере отображается отдельным состоянием без процента.
Импорт синхронный и не требует Celery. Полный пакет и реализация адаптера остаются
необходимыми для будущего запуска конкурсного движка; импорт четырёх CSV их не заменяет.

## Запуск и результат

`draft → queued → running → completed / failed / cancelled`.
Тот же Idempotency-Key возвращает сохранённый запуск, включая терминальный статус,
без новой задачи и пилотов. Другой ключ после старта: 409 `run_conflict`.
Блокировки строк проверены конкурентно на PostgreSQL. После сбоя публикации:
503 `execution_unavailable`, запуск становится failed с событием `queue_unavailable`.
Секреты, traceback и пути среды не входят в публичные ответы.

**Текущий блокер:** общий `Agent.act` не реализован; адаптер публичной среды не подключён.
По умолчанию `REDORDA_RUN_EXECUTION_ENABLED=0`, `REDORDA_ENVIRONMENT_FACTORY` пуст.
`meta.features.run_execution=false`, `start/` возвращает 503 `engine_unavailable`,
черновик остаётся draft, сообщения в Redis не отправляются. Для включения нужны
готовый агент, factory `(EngineContext) -> AgentEnvironment` и явное значение флага `1`.
При ошибочном включении неготовый движок также завершается `engine_unavailable` в worker.

`csv_export=true` обозначает реализацию экспорта сохранённых результатов;
`openai_strategy=false`, поскольку HTTP API пока принимает только baseline.
Worker не повторяет расчёт автоматически; используется prefork, soft limit 570 с,
hard limit 600 с и guard через 610 с после постановки в очередь. При потере всех
worker guard выполнится после восстановления worker; API не обещает мгновенной
фиксации сбоя при полной остановке инфраструктуры.

Перед каждым пилотом backend проверяет число пилотов, запрошенные контакты и
максимальную стоимость по публичным ценам каналов. Запрос, превышающий остаток,
отклоняется целиком. Ответ среды должен содержать фактические `cost`, `n_customers`.
Перед completed повторно проверяются сохранённые кампании и все известные расходы.
AI-разработчик отвечает за соблюдение лимитов публичной среды, выборку аудитории
финальных кампаний и проверку общих расходов при неизвестных backend метриках.

Результат: `{run_id, status, campaigns: [{rank, parameters, explanation, metrics}],
totals, warnings}`. Неизвестные расходы/контакты/эффекты — `null` с предупреждением;
прогноз и результат симуляции хранятся отдельно, эффект пересекающихся кампаний
не суммируется. Экспорт не запускает стратегию повторно и не является доказательством
прохождения официального оценщика. Подробнее: [backend-results.md](backend-results.md).
