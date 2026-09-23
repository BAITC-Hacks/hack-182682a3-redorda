# Пользовательский сценарий кампании

React 18.2: создание черновика → запуск → прогресс и пилоты → кампании → CSV.
В интерфейсе нет демонстрационных результатов: тестовые ответы находятся только в тестах.
`npm test` проверяет сценарий на границе HTTP, `npm run build` проверяет типы и сборку.

## Контракт подключения расчётов

Это предлагаемая форма ответов для этапа 2, необходимая подготовленному фронтенду.
Текущий Django реализует только черновики. При подключении backend следует согласовать
поля, обновить `docs/openapi.yaml` через `make schema` и `src/api/types.ts`.
Существующие маршруты и поля сохраняются согласно `docs/api-contract.md`.

- `meta.features.run_execution`: включает запуск/отмену и чтение журнала.
- `meta.features.csv_export`: включает скачивание завершённого плана.
- `GET runs/{id}/`: существующий `Run` и необязательные поля:
  `progress: {stage: string, percent: number | null, spent: decimal-string,
  contacts_used: integer, pilots_completed: integer} | null`,
  `error: {code: string, message: string} | null`, `cancellation_requested: boolean`.
  Все счётчики включают пилоты. Отсутствующий прогресс означает неизвестные показатели,
  а не нулевые расходы. `percent` находится в диапазоне 0–100; `null` означает неопределённый прогресс.
- `POST runs/{id}/start/`: пустой JSON, заголовок `Idempotency-Key`, ответ 202 с полным `Run`.
  Повтор с тем же ключом возвращает тот же запуск, в том числе после потери ответа.
  Ключ сохраняется в sessionStorage для конкретного плана. Сервер обязан гарантировать
  единственный запуск для плана независимо от вкладки и ключа.
- `POST runs/{id}/cancel/`: пустой JSON, ответ 202 с `Run` и
  `cancellation_requested: true` либо уже конечным статусом. Повтор безопасен.
  Запрос отмены не равен остановке: опрос продолжается до конечного статуса.
- `GET runs/{id}/events/?after=0`: стандартный `{count, next, previous, results}`.
  События имеют возрастающий числовой `id`, `created_at`, `kind` (`info`, `pilot`,
  `warning`, `error`), `message` и необязательный `pilot`:
  `{campaign_name, channel, target_tariff, customers, cost: decimal-string,
  observed_effect: decimal-string | null}`. Порядок по ID, курсор `after` эксклюзивный;
  при `next != null` следующая страница запрашивается с максимальным полученным ID.
- `GET runs/{id}/results/`: только после `completed`, иначе 409:
  `{campaigns: CampaignResult[], totals: {spent, contacts_used, pilots_completed,
  forecast_effect, simulated_effect}}`. Денежные показатели — decimal-строки;
  оба эффекта могут быть `null` (не рассчитаны). Эффект — прирост выручки за вычетом
  стоимости контактов, расходы включают пилоты. `CampaignResult` содержит `id`,
  `campaign_name`, `target_tariff`, `channel`, `audience` (читаемое описание),
  `customers`, `cost`, `forecast_effect: decimal-string | null`, `rationale`.
- `GET runs/{id}/export/`: CSV с `Content-Type: text/csv`, только после `completed`;
  ошибки остаются JSON по общему контракту. Фронтенд скачивает ответ сервера,
  не пересчитывает и не генерирует submission самостоятельно.

Чтение обновляется каждые 2 секунды без перекрывающихся запросов. При сетевой ошибке
сохраняются последние данные с предупреждением, активный расчёт продолжает опрашиваться.
После конечного статуса регулярный опрос останавливается; недоступные результаты/журнал
можно запросить повторно. Уход со страницы прерывает запросы. POST автоматически не повторяется.
CSRF передаётся из cookie `csrftoken`, если она присутствует; авторизацию и права обеспечивает backend.
Публичный proxy пока блокирует запись — интерфейс показывает ответ 403 с объяснением.
