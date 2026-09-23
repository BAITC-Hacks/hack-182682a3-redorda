# AI-движок: интеграция и запуск

`Agent().act(env)` и backend используют общий Python-движок. Подключение HTTP start/events/results/cancel, Celery и кнопки запуска в UI ещё не выполнено.

## Runner

```python
run_campaigns(env, config=None, *, history=None, observer=None,
              should_cancel=None, hypothesis_provider=None, options=None) -> EngineResult
```

Пример подключения:

```python
from campaign_engine.candidates import load_history
from campaign_engine.contracts import RunConfig
from campaign_engine.engine_types import EngineOptions
from campaign_engine.runner import run_campaigns

result = run_campaigns(
    env,
    RunConfig(budget=100_000, max_contacts=15_000, max_pilots=20,
              seed=42, strategy="baseline"),
    history=load_history(dataset.source_dir),
    observer=save_event,
    should_cancel=cancel_requested,
    options=EngineOptions(),
)
payload = result.to_dict()
```

Вызывающий код создаёт новую среду с нужным seed и пустой `pilot_history`. `dataset.source_dir` — абсолютный путь к датасету. `save_event(event)` сохраняет события, `cancel_requested() -> bool` сообщает об отмене.

`EngineOptions` задаёт внутренние настройки вычислений. Политики: `adaptive` по умолчанию, `fixed_100`, `fixed_200`, `wide_100` и экспериментальная `evolve`. Стандартный бюджет времени — 240 секунд, пилотный охват — до 2 000 контактов. HTTP-схема по-прежнему принимает только `strategy="baseline"`; режим `openai` доступен через runner.

## Результат и события

`EngineResult.to_dict()` возвращает JSON-совместимые данные:

| Поле | Содержимое |
| --- | --- |
| `status`, `stop_reason`, `warnings` | `completed`, `failed` или `cancelled`, причина завершения и предупреждения |
| `campaigns` | Словари финальных кампаний для CSV |
| `pilots` | Запрос, наблюдение, prior и posterior каждого подтверждённого пилота |
| `resource_usage` | Расходы и контакты пилотов, число подтверждённых наблюдений, плановые ресурсы финальных кампаний |
| `estimates` | Прогноз net, модельный нижний хвост, оценки кампаний |
| `metadata` | Версия движка, seed, настройки, источник гипотез и длительность |
| `events` | Журнал событий запуска |

Прогноз движка и результат симуляции сохраняются отдельно: результат evaluator добавляет backend или слой оценки. Нижний хвост прогноза учитывает неопределённость эффекта и усредняет неизвестные пилотные подвыборки. Это неполная оценка риска; доходность не гарантируется.

Формат события:

```json
{"sequence": 1, "type": "run_started", "data": {}}
```

Типы: `run_started`, `candidates_ready`, `hypotheses_ready`, `fallback_used`, `pilot_started`, `pilot_completed`, `pilot_failed`, `portfolio_updated`, `run_completed`, `run_cancelled`, `run_failed`. `sequence` возрастает внутри запуска. Backend назначает постоянный ID для параметра API `after`, timestamp и run ID.

## Выполнение, отмена и восстановление

- Backend обеспечивает `Idempotency-Key` и один активный worker. Повторный вызов runner выполняет действия заново, поэтому автоматический retry всей задачи после пилота недопустим.
- Отмена проверяется перед внешними действиями, после вызова модели и между пилотами. Начатый синхронный `run_pilot` завершается; его результат и расходы сохраняются.
- Ошибка `observer` останавливает действия с `stop_reason="observer_failed_do_not_retry"`. Для восстановления требуется сверка сохранённого журнала и среды.
- При ошибке пилота с изменившимися счётчиками/историей либо некорректном наблюдении запуск завершается как `failed`. Доступные расходы остаются в `resource_usage`, запрос для сверки — в `metadata.unreconciled_pilot_request`. Число подтверждённых наблюдений хранится отдельно.
- Перед пилотами проверяется возможность оставить ресурс для финальной кампании. Меньший лимит конфигурации должен выражаться доступными фильтрами: произвольный `n_customers` в CSV отсутствует. Невыполнимая конфигурация отклоняется.
- `Agent.act` возвращает кампании при `completed` и поднимает `EngineFailure` при `failed` или `cancelled`.

## OpenAI, fallback и replay

`RunConfig(strategy="openai")` включает один запрос гипотез через Responses API. На вход поступают агрегаты; runner проверяет тарифы и аудитории предложений. Оценки эффекта, пилоты, расходы и портфель вычисляются кодом. При ошибке провайдера или непригодном ответе runner продолжает расчётную стратегию и записывает `fallback_used`.

Настройки в серверном `.env`: `OPENAI_API_KEY`, `OPENAI_MODEL` (по умолчанию `gpt-6-sol`), `OPENAI_REASONING_EFFORT`, `OPENAI_TIMEOUT_SECONDS`. Timeout запроса ограничен 30 секундами и остатком времени запуска. `PlanningUnavailable.code` и `.http_status` позволяют различать ошибки конфигурации, соединения, доступа, лимитов и формата ответа.

Проверка API сохраняет предложения в `artifacts/openai_hypotheses_replay.json`:

```bash
.venv/bin/python scripts/check_openai.py
```

Replay применяется через provider:

```python
from campaign_engine.openai_gateway import replay_hypotheses

result = run_campaigns(
    env,
    config,
    history=history,
    hypothesis_provider=lambda summary: replay_hypotheses(summary, saved_replay),
)
```

Replay проверяет SHA-256 агрегатов и версии prompt/формата; модель записывается в метаданные. Изменение входов вызывает fallback. `Agent()` по умолчанию использует расчётную политику без сети; replay подключается явно.

Документация API: [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol).

## Проверки и сборка

```bash
.venv/bin/python scripts/run_official.py local_eval --runs 10
.venv/bin/python scripts/run_official.py make_submission
.venv/bin/python scripts/build_agent.py --submission artifacts/submission.csv
.venv/bin/python scripts/benchmark_agent.py --runs 10 --policies adaptive fixed_100 template
make check
```

Скрипт запуска проверяет загрузку нужного `Agent`, выполнение пилотов, финальные кампании и лимиты. Перехваченное evaluator исключение агента считается неуспешной проверкой.

`artifacts/delivery/` содержит автономный `agent.py`, `submission.csv`, зависимости с фиксированными версиями, README и manifest. Агент собирается из модулей общего движка; тест сравнивает пакетную и автономную версии в изолированном процессе.

Benchmark сохраняет отчёт в `artifacts/benchmark.json`, включая ошибочные запуски. Статистики net в summary рассчитаны по валидным запускам, число невалидных указано отдельно. Результаты относятся к проверенным локальным сценариям.

Текущие ограничения: SMS-пилоты, до одного финального варианта на базовую клетку, жадный выбор портфеля. Пересечения пилотов оцениваются вероятностно, call — консервативной границей насыщения. История ранжирует переходы; эффект уточняется пилотами. `evolve` использует ограниченный набор прогнозных исходов и действий и остаётся экспериментальной политикой.
