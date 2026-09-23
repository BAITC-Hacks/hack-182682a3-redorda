# REST API Django

Base URL: `/api/v1/`. JSON-запросы и ответы используют UTF-8. UUID передаются строками, время — ISO 8601. Бюджет и суммы расходов в `Run`, `metrics.cost` и `totals` — десятичные строки; прогнозные показатели и вложенные публичные наблюдения сохраняют числовой JSON-формат. API запускает расчёт через очередь. Проверка и импорт CSV выполняются синхронно с ограничением размера.

## Сессия и CSRF

При `DJANGO_DEBUG=0` данные и операции требуют активной Django-сессии. В DEBUG
это включается через `REDORDA_REQUIRE_AUTH=1`. `GET auth/me/` возвращает
`{authenticated, user}`; `user` равен `null` либо содержит `id`, `username`, `is_staff`.
`GET auth/csrf/` возвращает `{csrf_token}`. `POST auth/login/` принимает
`{username, password}` и `X-CSRFToken`, возвращает сессию и новый `csrf_token`.
`POST auth/logout/` возвращает анонимную сессию и новый токен.

Клиент использует один origin и `credentials: 'same-origin'`; все POST отправляются
с действующим `X-CSRFToken`. После login используется токен из его ответа.
Неактивный пользователь или неверный пароль дают 401 `invalid_credentials`;
нет сессии — 403 `not_authenticated`; неверный CSRF — 403 `csrf_failed` на auth POST
или `permission_denied` на остальных POST. Ограничение входа — 10 попыток/минуту
на IP, затем 429 `throttled`. Подробнее: [api-contract.md](api-contract.md).

## Существующие операции

| Метод | Путь | Успех | Назначение |
| --- | --- | --- | --- |
| GET | `health/` | 200 `{status: "ok", service: "redorda-api"}` | Публичная проверка API и соединения с БД. |
| GET | `ready/` | 200 `{status: "ok", database: true, broker: true}` | Публичная проверка БД и Redis; при недоступности — 503 и соответствующие boolean-поля. |
| GET | `meta/` | 200 | `limits`, `channel_costs`, `features`; флаги зависят от конфигурации сервера. |
| GET | `datasets/current/` | 200 | Текущий импортированный набор; 404 без набора. |
| POST | `datasets/import-demo/` | 201 / 200, `Dataset` | Импорт встроенных четырёх CSV; пустое тело или `{}`. |
| POST | `datasets/import/` | 201 / 200, `Dataset` | Четыре CSV в повторяющемся multipart-поле `files`. |
| GET | `runs/` | 200 | `{count, next, previous, results}`; стандартная пагинация DRF, 20 элементов на страницу. |
| POST | `runs/` | 201 | Создать черновик; 409 без набора. |
| GET | `runs/{id}/` | 200 | Сохранённый план; 404 для неизвестного UUID. |

`POST runs/` принимает объект с обязательным `name` (непустая строка до 120 символов) и необязательными полями:

| Поле | Тип | По умолчанию | Допустимые значения |
| --- | --- | --- | --- |
| `budget` | decimal-строка | `"100000.00"` | 0.01–100000.00, не более двух знаков после точки |
| `max_contacts` | целое | 15000 | 1–15000 |
| `max_pilots` | целое | 20 | 1–20 |
| `seed` | целое | 42 | 0–2147483647 |
| `strategy` | строка | `baseline` | `baseline` или `openai`; создание OpenAI-плана требует `features.openai_strategy=true` |

`features.run_execution=true` требует `REDORDA_RUN_EXECUTION_ENABLED=1` и непустого
`REDORDA_ENVIRONMENT_FACTORY`. `features.openai_strategy` дополнительно требует
серверного `OPENAI_API_KEY`; наличие ключа не подтверждает доступ к модели.
`features.csv_export=true` обозначает реализацию экспорта. Эти флаги не проверяют
worker, данные или внешнюю сеть. Создание недоступной OpenAI-стратегии даёт
400 `validation_error` с подробностью в `fields.strategy`.

Неизвестные поля, в том числе `status`, отклоняются. Каждый объект `Run` содержит `id`, `name`, `dataset_id`, `status`, `budget`, `max_contacts`, `max_pilots`, `seed`, `strategy`, `created_at`, `progress`, `error`, `cancellation_requested`. Статусы: `draft`, `queued`, `running`, `completed`, `failed`, `cancelled`.

`progress` имеет поля `stage` (текущий статус или `finalizing`, когда при `running` уже сохранены кампании), `percent` (0–100), `spent_budget` (decimal-строка учтённых расходов пилотов и кампаний либо `null`), `used_contacts` (учтённые контакты либо `null`), `completed_pilots` (число сохранённых пилотов). Если стоимость или контакты хотя бы одной сохранённой кампании отсутствуют, соответствующее поле равно `null`. До старта процент равен 0, после `completed` — 100; в остальных состояниях это оценка по числу пилотов относительно лимита и по началу сохранения кампаний, потому что агент вправе завершить работу раньше. `error` равен `null`, кроме `failed`, где содержит безопасные `code` и `message` без текста внутреннего исключения. `cancellation_requested` показывает принятый запрос остановки отдельно от статуса: при `running` он становится `true` до перехода в `cancelled`.

## Операции расчёта

| Метод | Путь | Успех | Поведение |
| --- | --- | --- | --- |
| POST | `runs/{id}/start/` | 202, объект `Run` | Требует заголовок `Idempotency-Key` из 1–255 непустых символов. Сервис исполнения ставит ровно одну задачу в очередь. Повтор с тем же ключом возвращает сохранённый run; конфликт ключа или состояния — 409; недоступная очередь — 503. Тело не требуется. |
| POST | `runs/{id}/cancel/` | 200, объект `Run` | Запрашивает остановку. Повтор в подходящем/терминальном состоянии возвращает текущий run; конфликт состояния — 409. Тело не требуется. |
| GET | `runs/{id}/events/?after=0&limit=100` | 200 | События этого run с `id > after`, по возрастанию `id`. `after` — целое >= 0, `limit` — целое 1–200. |
| GET | `runs/{id}/results/` | 200 | Сохранённый результат завершённого расчёта. Пока не готов — 409. |
| GET | `runs/{id}/export/` | 200 `text/csv` | Потоковый CSV завершённого расчёта, `Content-Disposition: attachment`. Иной статус — 409. |

Новый запуск при отключённой расчётной стратегии даёт 503 `engine_unavailable`;
недоступный OpenAI-запуск — 503 `openai_unavailable`. В этих случаях план остаётся
`draft`, задача не публикуется. Уже начатый запуск с прежним `Idempotency-Key`
возвращается независимо от текущего флага функции; повторных пилотов не возникает.

Страница событий имеет вид `{results: RunEvent[], next_after: integer, has_more: boolean}`. `RunEvent` содержит `id` (возрастающее целое), `kind` (строка), `payload` (JSON-объект), `created_at` (ISO 8601). `next_after` равен ID последнего события страницы, а для пустой страницы — входному `after`; отсутствие новых событий возвращает 200 с `results: []` и `has_more: false`. Приватные поля события, такие как `task_id`, токены, ключи, пути и traceback, не выдаются.

События добавляются без изменения старых ID. `pilot_completed` сохраняет
`{sequence, channel, requested_customers, n_customers, cost, request, observation}`
после ответа среды. `observation` включает доступные публичные поля
`n_customers`, `cost`, `observed_lift_ratio`, `observed_lift_total`,
`remaining_budget`, `remaining_contacts`. Для исторических событий `request`
и `observation` могут отсутствовать. `pilot_estimate_updated` отдельно сохраняет
`{sequence, posterior}` после обновления оценки. Событие движка `run_completed`
ещё не является финальным статусом: UI ориентируется на `Run.status`.

Структура ответа результата; значения ниже иллюстрируют формат:

```json
{
  "run_id": "UUID",
  "status": "completed",
  "campaigns": [{"rank": 1, "parameters": {}, "explanation": "...", "metrics": {}}],
  "totals": {
    "pilot_cost": "0.00",
    "campaign_cost": "0.00",
    "total_cost": "0.00",
    "pilot_contacts": 0,
    "total_contacts": 0,
    "predicted_effect": null,
    "simulator_result": null
  },
  "warnings": []
}
```

`campaign_cost`, `total_cost`, `total_contacts`, `predicted_effect` и `simulator_result` могут быть `null`, если расчёт их не предоставил; `warnings` объясняет отсутствие данных. `parameters` включает фильтры аудитории, `target_tariff` и `channel` по публичному контракту движка. `metrics.cost` и `metrics.n_contacts` всегда есть, но равны `null`, если движок их не предоставил; `explanation` также может быть `null`. Прогноз и результат симуляции сохраняются отдельно в `totals`. Отсутствующие расчёты не заменяются выдуманными значениями. Экспорт содержит заголовок и до 10 строк кампаний с колонками `campaign_name,filter_arpu_segment,filter_data_segment,filter_call_segment,filter_current_tariff,target_tariff,channel`.

Интегрированный движок сохраняет для каждой кампании `n_contacts`, `cost` и
`estimated_incremental_net`. API нормализует `cost` в decimal-строку; прогнозный
`estimated_incremental_net` остаётся числом. Текущий `totals.predicted_effect`:

| Поле | Тип | Значение |
| --- | --- | --- |
| `source` | string | `posterior_forecast_not_official_score` |
| `net_arpu_gain_mean` | number | Средний прогноз чистого эффекта всего портфеля |
| `lower_tail_mean_10` | number | Среднее в нижних 10% модельных исходов, не доверительный интервал |
| `objective` | number | Целевая функция оптимизатора с учётом риска |

`simulator_result` остаётся `null`: веб-пайплайн не рассчитывает отдельную итоговую
оценку симулятора. В `warnings` сохраняются ограничения прогноза и сообщения
fallback. CSV и `results/` читают сохранённые записи, не выполняя расчёт повторно.

## Живая команда и команды

`GET runs/{id}/team/` возвращает согласованный snapshot. `last_event_id` — курсор:
события до него уже включены в состояние, следующий запрос —
`GET runs/{id}/events/?after=7`. Пример формы (значения иллюстративны):

```json
{"schema_version":1,"run_id":"00000000-0000-0000-0000-000000000001",
 "snapshot_id":"00000000-0000-0000-0000-000000000002","last_event_id":7,
 "tasks":[],"artifacts":[],"available_commands":["create_plan"]}
```

Задачи содержат `id`, `actor_id`, `status`, `title`, `artifact_ids`,
`evidence_ids`. Артефакты содержат `id`, `task_id`, `type`, `title`, `data`,
`evidence_ids`. Состав зависит от сохранённого запуска; пустые списки означают
отсутствие подтверждённой работы, а не выполненный расчёт. `available_commands`
формирует сервис по текущему состоянию и наличию функций движка. Сейчас
`explain`/`compare` ещё не подключены и в списке не появляются. Поля внутреннего состояния движка, секреты,
пути и traceback не выдаются. Неизвестный измеренный результат остаётся `null`.

`POST runs/{id}/commands/` требует сессию, CSRF и `Idempotency-Key` длиной
1–255. Тело имеет только `type`, `snapshot_id`, `parameters`:

```http
Idempotency-Key: explain-campaign-1
X-CSRFToken: <токен сессии>

{"type":"explain","snapshot_id":"00000000-0000-0000-0000-000000000002",
 "parameters":{"campaign_id":"campaign-1"}}
```

Другие допустимые параметры:

```json
{"type":"compare","snapshot_id":"00000000-0000-0000-0000-000000000002",
 "parameters":{"constraints":{"budget":"50000.00","allowed_channels":["sms"]}}}
```

```json
{"type":"create_plan","snapshot_id":"00000000-0000-0000-0000-000000000002",
 "parameters":{"name":"Меньший бюджет","constraints":{"budget":"50000.00"}}}
```

`budget` — decimal-строка от `0.01` до `100000.00`; каналы: `push`, `sms`,
`digital_ads`, `call`. В `constraints` нужен хотя бы один параметр. Неизвестные
поля на любом уровне, пустые значения и неверные типы дают 400.
`snapshot_id` и ID команды — UUID. Для `create_plan` ограничение каналов пока
не поддерживается движком: команда завершится `failed/capability_unavailable`,
новый план не создаётся. Примеры `explain`/`compare` описывают контракт подключения
AI; до реализации функций они также завершаются `failed/capability_unavailable`.

Первый ответ — 202, повтор того же запроса с тем же ключом возвращает тот же
объект; другой запрос с этим ключом — 409. `GET
runs/{id}/commands/{command_id}/` возвращает его текущее состояние:

```json
{"id":"00000000-0000-0000-0000-000000000003","type":"explain","status":"queued",
 "result":null,"error":null}
```

Статусы: `queued`, `running`, `completed`, `failed`. У `explain`/`compare`
результат содержит сохранённый артефакт с основаниями; `create_plan` — новый
`run_id` черновика, который запускается отдельно через `start/`.
Проверку принадлежности команды исходному run выполняет сервис и HTTP-слой.
Неподдерживаемые команды `pause`, `resume`, ручной пилот отклоняются с 400.

Ошибки: отсутствие ключа — 400 `validation_error` с
`fields.Idempotency-Key`; отсутствующий или чужой snapshot/команда — 404 `not_found`;
повтор ключа с другим телом — 409 `command_conflict`; недоступный сервис —
503 `team_unavailable`. При отказе Redis команда сохраняется с
`failed/queue_unavailable`, при превышении времени — `failed/timeout`.
Поздний ответ worker не перезаписывает терминальное состояние и не публикует артефакт.
Команды работают через интегрированные сервисы `team_state`/`team_commands`.

## Ошибки

Все обрабатываемые ошибки имеют вид `{ "error": { "code": "validation_error", "message": "Проверьте параметры запроса.", "fields": { "budget": ["..."] } } }`. Для ошибок состояния `fields` пустой объект. Основные коды: `validation_error` (400), `not_found` (404), `dataset_required`, `run_conflict`, `result_not_ready` (409), `engine_unavailable`, `openai_unavailable`, `execution_unavailable` (503). Внутренние подробности исключений сервисов, пути файлов и traceback не включаются в сообщения. `health/`, `ready/`, `/api/schema/` и `/api/docs/` остаются публичными.

Схема OpenAPI доступна через `/api/schema/`; Swagger UI — `/api/docs/`.
`make schema` обновляет `docs/openapi.yaml` с проверкой схемы. Соответствующие
TypeScript-типы находятся в `frontend/src/api/types.ts`, в том числе
`strategy: 'baseline' | 'openai'` в `Run` и `RunInput`.


`meta.environment` содержит `{mode, label}` с пояснением среды. Для встроенного
локального симулятора с проверкой SHA-256 доступность нового старта также зависит
от наличия установленного пакета.

`Run.execution_blocker` — причина недоступности расчёта для набора либо `null`.
Четыре сырых CSV без профиля не запускаются в обеих встроенных средах; очередь не
вызывается. Формат импорта описан в [контракте](api-contract.md#импорт-из-интерфейса).

CSV-клиент передаёт `Accept: text/csv, application/json`: успешный ответ — поток CSV,
ошибки — JSON. Один `Accept: text/csv` не проходит JSON content negotiation DRF.
