import type { ApiRun, ApiRunEvent, Channel, Run, RunEvent, RunResult, RunResults } from './types';

const stages: Record<string, string> = {
  draft: 'План подготовлен', queued: 'Ожидаем свободного исполнителя', running: 'Проверяем кампании',
  finalizing: 'Сохраняем итоговый план', completed: 'Расчёт завершён', failed: 'Ошибка расчёта', cancelled: 'Расчёт остановлен',
};
const scalar = (value: unknown): string | null =>
  (typeof value === 'number' || (typeof value === 'string' && value.trim() !== '')) && Number.isFinite(Number(value)) ? String(value) : null;
const count = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null;
const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' ? value as Record<string, unknown> : {};

export function toRun(value: ApiRun): Run {
  const p = value.progress;
  return { ...value, progress: p ? {
    stage: stages[p.stage] ?? p.stage, percent: p.percent, spent: p.spent_budget,
    contacts_used: p.used_contacts, pilots_completed: p.completed_pilots,
  } : null };
}

export function toEvent(value: ApiRunEvent): RunEvent {
  const messages: Record<string, string> = {
    run_created: 'План создан', run_queued: 'Расчёт поставлен в очередь', run_started: 'Расчёт начат',
    pilot_started: 'Пилот начат', pilot_completed: 'Пилот завершён', result_ready: 'Финальный план сохранён',
    run_completed: 'Расчёт завершён', run_failed: 'Расчёт завершился с ошибкой',
    cancellation_requested: 'Запрошена остановка', run_cancelled: 'Расчёт остановлен',
    campaign_selected: 'Кампания включена в план', pilot_estimate_updated: 'Прогноз по пилоту обновлён',
    queued: 'Расчёт поставлен в очередь', running: 'Расчёт начат', completed: 'Расчёт завершён',
    failed: 'Расчёт завершился с ошибкой', cancelled: 'Расчёт остановлен',
    cancel_requested: 'Запрошена остановка', portfolio_updated: 'Портфель кампаний обновлён',
    fallback_used: 'Используются расчётные гипотезы', campaign_result: 'Результат кампании сохранён',
  };
  const payload = value.payload;
  const request = record(payload.request);
  const observation = payload.observation ? record(payload.observation) : payload;
  const channel = request.channel ?? payload.channel;
  const pilot = value.kind === 'pilot_completed' && ['push', 'sms', 'digital_ads', 'call'].includes(String(channel)) ? {
    campaign_name: typeof request.campaign_name === 'string' ? request.campaign_name : 'Проверка гипотезы',
    channel: channel as Channel, target_tariff: String(request.target_tariff ?? payload.target_tariff ?? 'Тариф не указан'),
    customers: count(observation.n_customers), cost: scalar(observation.cost),
    observed_lift_ratio: count(observation.observed_lift_ratio),
  } : null;
  return { id: value.id, created_at: value.created_at, event_kind: value.kind, payload: value.payload,
    kind: ['run_failed', 'failed'].includes(value.kind) ? 'error' : value.kind === 'warning' ? 'warning' : pilot ? 'pilot' : 'info',
    message: messages[value.kind] ?? `Событие: ${value.kind}`, pilot };
}

export function toResults(value: RunResult): RunResults {
  const labels: Record<string, string> = { filter_arpu_segment: 'ARPU', filter_data_segment: 'Интернет', filter_call_segment: 'Звонки', filter_current_tariff: 'Текущий тариф' };
  return {
    campaigns: value.campaigns.map(c => ({
      id: String(c.rank), campaign_name: c.parameters.campaign_name, target_tariff: c.parameters.target_tariff,
      channel: c.parameters.channel,
      audience: Object.entries(c.parameters).filter(([key]) => key in labels).map(([key, filter]) => `${labels[key]}: ${filter}`).join(' · ') || 'Все подходящие клиенты',
      customers: count(c.metrics.n_contacts), cost: scalar(c.metrics.cost), forecast_effect: scalar(c.metrics.predicted_effect ?? c.metrics.estimated_incremental_net),
      rationale: c.explanation || 'Обоснование не предоставлено',
    })),
    warnings: value.warnings,
    totals: { spent: scalar(value.totals.total_cost), contacts_used: count(value.totals.total_contacts),
      forecast_effect: scalar(record(value.totals.predicted_effect).net_arpu_gain_mean ?? value.totals.predicted_effect), simulated_effect: scalar(value.totals.simulator_result),
      lower_tail_mean_10: scalar(record(value.totals.predicted_effect).lower_tail_mean_10) },
  };
}
