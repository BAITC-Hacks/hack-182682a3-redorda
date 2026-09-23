# REST API Django

Base URL: `/api/v1/`. JSON-запросы и ответы используют UTF-8. UUID передаются строками, время — ISO 8601, денежные суммы — десятичными строками. API запускает расчёт кампаний через очередь. Проверка и импорт CSV выполняются синхронно с ограничением размера.

## Существующие операции

| Метод | Путь | Успех | Назначение |
| --- | --- | --- | --- |
| GET | `health/` | 200 `{status: "ok", service: "redorda-api"}` | Публичная проверка API и соединения с БД. |
| GET | `meta/` | 200 | `limits`, `channel_costs`, `features`. Флаги остаются `false`, пока интегрированный сценарий не проверен. |
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
| `strategy` | строка | `baseline` | Только `baseline` до подключения стратегии OpenAI |

Неизвестные поля, в том числе `status`, отклоняются. Каждый объект `Run` содержит `id`, `name`, `dataset_id`, `status`, `budget`, `max_contacts`, `max_pilots`, `seed`, `strategy`, `created_at`. Статусы: `draft`, `queued`, `running`, `completed`, `failed`, `cancelled`.

## Операции расчёта

| Метод | Путь | Успех | Поведение |
| --- | --- | --- | --- |
| POST | `runs/{id}/start/` | 202, объект `Run` | Требует заголовок `Idempotency-Key` из 1–255 непустых символов. Сервис исполнения ставит ровно одну задачу в очередь. Повтор с тем же ключом возвращает сохранённый run; конфликт ключа или состояния — 409; недоступная очередь — 503. Тело не требуется. |
| POST | `runs/{id}/cancel/` | 200, объект `Run` | Запрашивает остановку. Повтор в подходящем/терминальном состоянии возвращает текущий run; конфликт состояния — 409. Тело не требуется. |
| GET | `runs/{id}/events/?after=0&limit=100` | 200 | События этого run с `id > after`, по возрастанию `id`. `after` — целое >= 0, `limit` — целое 1–200. |
| GET | `runs/{id}/results/` | 200 | Сохранённый результат завершённого расчёта. Пока не готов — 409. |
| GET | `runs/{id}/export/` | 200 `text/csv` | Потоковый CSV завершённого расчёта, `Content-Disposition: attachment`. Иной статус — 409. |

Страница событий имеет вид `{results: RunEvent[], next_after: integer, has_more: boolean}`. `RunEvent` содержит `id` (возрастающее целое), `kind` (строка), `payload` (JSON-объект), `created_at` (ISO 8601). `next_after` равен ID последнего события страницы, а для пустой страницы — входному `after`; отсутствие новых событий возвращает 200 с `results: []` и `has_more: false`. Приватные поля события, такие как `task_id`, токены, ключи, пути и traceback, не выдаются.

Ответ результата:

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

`campaign_cost`, `total_cost`, `total_contacts`, `predicted_effect` и `simulator_result` могут быть `null`, если расчёт их не предоставил; `warnings` объясняет отсутствие данных. `parameters` — параметры кампании по публичному контракту движка; `metrics` и значения прогноза/симуляции — сохранённые сервисом результата JSON-объекты. Отсутствующие расчёты не заменяются выдуманными значениями. Экспорт содержит заголовок и до 10 строк кампаний с колонками `campaign_name,filter_arpu_segment,filter_data_segment,filter_call_segment,filter_current_tariff,target_tariff,channel`.

## Ошибки

Формат, лимиты, повторный импорт и поля сводки четырёх CSV описаны в
[контракте импорта](api-contract.md#импорт-из-интерфейса).

Все обрабатываемые ошибки имеют вид `{ "error": { "code": "validation_error", "message": "Проверьте параметры запроса.", "fields": { "budget": ["..."] } } }`. Для ошибок состояния `fields` пустой объект. Основные коды: `validation_error` (400), `not_found` (404), `dataset_required`, `run_conflict`, `result_not_ready` (409), `execution_unavailable` (503). Внутренние подробности исключений сервисов, пути файлов и traceback не включаются в сообщения. Проверка доступа для пользовательских операций задаётся интегратором; `health/` остаётся публичным.

Схема OpenAPI доступна через `/api/schema/`. Для проверки изменения API генерируйте её во временный файл; общий `docs/openapi.yaml` обновляет интегратор.
