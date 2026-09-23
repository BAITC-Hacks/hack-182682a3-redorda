import { describe, expect, it } from 'vitest';
import { toEvent, toResults, toRun } from './adapters';
import type { ApiRun, RunResult } from './types';

describe('backend wire contract', () => {
  it('preserves unknown progress instead of inventing zero spending', () => {
    const run = toRun({ progress: { stage: 'finalizing', percent: 95, spent_budget: null, used_contacts: null, completed_pilots: 2 } } as ApiRun);
    expect(run.progress).toEqual({ stage: 'Сохраняем итоговый план', percent: 95, spent: null, contacts_used: null, pilots_completed: 2 });
  });

  it('keeps a pilot lift ratio distinct from monetary effect', () => {
    const event = toEvent({ id: 1, kind: 'pilot_completed', created_at: '', payload: {
      request: { channel: 'push', target_tariff: 'tariff_4' }, observation: { n_customers: 10, cost: 10, observed_lift_ratio: 0.25 },
    } });
    expect(event.pilot).toMatchObject({ customers: 10, cost: '10', observed_lift_ratio: 0.25 });
  });

  it('preserves null metrics, warnings and unavailable explanations', () => {
    const wire: RunResult = { run_id: '1', status: 'completed',
      campaigns: [{ rank: 1, parameters: { campaign_name: 'A', target_tariff: 'tariff_4', channel: 'push' }, explanation: null, metrics: { cost: null, n_contacts: null } }],
      totals: { pilot_cost: '0.00', campaign_cost: null, total_cost: null, pilot_contacts: 0, total_contacts: null, predicted_effect: { unknown: true }, simulator_result: null },
      warnings: ['Недостаточно наблюдений'],
    };
    const result = toResults(wire);
    expect(result.totals).toEqual({ spent: null, contacts_used: null, forecast_effect: null, simulated_effect: null });
    expect(result.campaigns[0]).toMatchObject({ cost: null, customers: null, forecast_effect: null, rationale: 'Обоснование не предоставлено' });
    expect(result.warnings).toEqual(wire.warnings);
  });
});
