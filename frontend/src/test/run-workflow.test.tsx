import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';

const draft = { id: 'plan-1', name: 'Осенняя кампания', dataset_id: 'data-1', status: 'draft',
  budget: '100000.00', max_contacts: 15000, max_pilots: 20, seed: 42, strategy: 'baseline', created_at: '2026-09-23T10:00:00Z' };
const progress = { stage: 'Проверяем гипотезы', percent: 40, spent: '1200.50', contacts_used: 100, pilots_completed: 2 };
const event = { id: 1, kind: 'pilot', message: 'Проверена гипотеза перехода', created_at: '2026-09-23T10:01:00Z',
  pilot: { campaign_name: 'Интернет для активных', channel: 'sms', target_tariff: 'tariff_4', customers: 100, cost: '400.00', observed_effect: '250.00' } };
const results = { campaigns: [{ id: 'c-1', campaign_name: 'Интернет для активных', target_tariff: 'tariff_4', channel: 'sms', audience: 'Активные пользователи интернета', customers: 3000, cost: '12000.00', forecast_effect: '4500.00', rationale: 'Пилот показал положительный эффект.' }],
  totals: { spent: '13200.50', contacts_used: 3100, pilots_completed: 2, forecast_effect: '4500.00', simulated_effect: null } };
let status: string;
let enabled: boolean;
let calls: { path: string; init?: RequestInit }[];
let override: ((path: string, init?: RequestInit) => Response | Promise<Response> | undefined) | undefined;
const json = (body: unknown, statusCode = 200) => new Response(JSON.stringify(body), { status: statusCode, headers: { 'Content-Type': 'application/json' } });

beforeEach(() => {
  status = 'draft'; enabled = true; calls = []; override = undefined;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.replace('/api/v1/', ''); calls.push({ path, init });
    const custom = override?.(path, init); if (custom) return custom;
    if (path === 'meta/') return json({ limits: { budget: 100000, contacts: 15000, pilots: 20 }, channel_costs: {}, features: { run_execution: enabled, openai_strategy: false, csv_export: enabled } });
    if (path === 'datasets/current/') return json({ id: 'data-1', name: 'Набор', checksum: 'abc', customer_count: 10000, imported_at: draft.created_at, summary: { baseline_arpu: '100.00', tariff_count: 21, synthetic: true, segments: { arpu_segment: {}, data_segment: {}, call_segment: {} } } });
    if (path === 'runs/plan-1/') return json({ ...draft, status, ...(status !== 'draft' ? { progress } : {}) });
    if (path === 'runs/plan-1/start/') { status = 'queued'; return json({ ...draft, status }, 202); }
    if (path === 'runs/plan-1/cancel/') return json({ ...draft, status, progress, cancellation_requested: true }, 202);
    if (path.startsWith('runs/plan-1/events/')) return json({ count: 1, next: null, previous: null, results: [event] });
    if (path === 'runs/plan-1/results/') return json(results);
    if (path === 'runs/') return json(draft, 201);
    throw new Error(`Unexpected request: ${path}`);
  }));
});

function open(path = '/runs/plan-1') { render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>); }
const tick = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); };

describe('пользовательский сценарий', () => {
  it('создаёт черновик и открывает доступный запуск', async () => {
    open('/runs/new');
    fireEvent.change(await screen.findByLabelText('Название плана'), { target: { value: 'Осенняя кампания' } });
    fireEvent.click(screen.getByRole('button', { name: /Сохранить план/ }));
    expect(await screen.findByRole('button', { name: 'Запустить расчёт' })).toBeEnabled();
    const request = calls.find(call => call.path === 'runs/');
    expect(JSON.parse(String(request?.init?.body))).toMatchObject({ name: 'Осенняя кампания', budget: '100000', max_contacts: 15000 });
  });

  it('не запускает неподключённый backend и не выдаёт неизвестные расходы за ноль', async () => {
    enabled = false; open();
    expect(await screen.findByRole('button', { name: 'Запустить расчёт' })).toBeDisabled();
    expect(screen.getByText(/Расчёты пока недоступны/)).toBeInTheDocument();
    expect(calls.some(call => /events|results|start/.test(call.path))).toBe(false);
  });

  it('защищает от двойного клика, опрашивает прогресс и останавливается после результата', async () => {
    open(); const start = await screen.findByRole('button', { name: 'Запустить расчёт' });
    vi.useFakeTimers();
    fireEvent.click(start); fireEvent.click(start);
    await act(async () => {});
    expect(calls.filter(call => call.path.endsWith('/start/'))).toHaveLength(1);
    status = 'running'; await tick();
    expect(screen.getByRole('progressbar', { name: 'Прогресс расчёта' })).toHaveAttribute('aria-valuenow', '40');
    expect(screen.getByText('Проверена гипотеза перехода')).toBeInTheDocument();
    expect(screen.getByLabelText('Остаток бюджета')).toHaveTextContent(/98\s?799,5/);
    await tick();
    expect(screen.getAllByText('Проверена гипотеза перехода')).toHaveLength(1);
    status = 'completed'; await tick();
    expect(screen.getByRole('table', { name: 'Выбранные кампании' })).toHaveTextContent('tariff_4');
    expect(screen.getByLabelText('Результат симуляции')).toHaveTextContent('Не рассчитан');
    const count = calls.length; await tick(); expect(calls).toHaveLength(count);
  });

  it('сохраняет ключ запуска при потере ответа и повторном открытии страницы', async () => {
    override = path => path.endsWith('/start/') ? Promise.reject(new TypeError('Failed to fetch')) : undefined;
    const view = render(<MemoryRouter initialEntries={['/runs/plan-1']}><App /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Запустить расчёт' }));
    await screen.findByRole('alert'); view.unmount();
    override = undefined; open();
    fireEvent.click(await screen.findByRole('button', { name: 'Запустить расчёт' }));
    await waitFor(() => expect(calls.filter(call => call.path.endsWith('/start/'))).toHaveLength(2));
    const keys = calls.filter(call => call.path.endsWith('/start/')).map(call => new Headers(call.init?.headers).get('Idempotency-Key'));
    expect(keys[0]).toBeTruthy(); expect(keys[1]).toBe(keys[0]);
  });

  it('продолжает опрос после запроса отмены до подтверждения сервера', async () => {
    status = 'running'; open();
    const cancel = await screen.findByRole('button', { name: 'Остановить расчёт' });
    vi.useFakeTimers();
    fireEvent.click(cancel);
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить остановку' }));
    await act(async () => {});
    expect(screen.getByRole('button', { name: 'Остановка запрошена' })).toBeDisabled();
    status = 'cancelled'; await tick();
    expect(screen.getByText('Расчёт остановлен')).toBeInTheDocument();
    expect(calls.some(call => call.path.endsWith('/results/'))).toBe(false);
    const count = calls.length; await tick(); expect(calls).toHaveLength(count);
  });

  it('сохраняет данные при сетевом сбое и восстанавливает опрос', async () => {
    vi.useFakeTimers(); status = 'running'; open(); await act(async () => {});
    expect(screen.getByText('Проверена гипотеза перехода')).toBeInTheDocument();
    override = path => path === 'runs/plan-1/' ? Promise.reject(new TypeError('Failed to fetch')) : undefined;
    await tick();
    expect(screen.getByRole('alert')).toHaveTextContent(/Не удалось обновить/);
    expect(screen.getByText('Проверена гипотеза перехода')).toBeInTheDocument();
    override = undefined; await tick();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('продолжает опрос, если первое чтение после принятого запуска не удалось', async () => {
    open(); const start = await screen.findByRole('button', { name: 'Запустить расчёт' });
    vi.useFakeTimers();
    override = path => path === 'runs/plan-1/' ? Promise.reject(new TypeError('offline')) : undefined;
    fireEvent.click(start); await act(async () => {});
    expect(screen.getByRole('alert')).toHaveTextContent(/Не удалось обновить/);
    override = undefined; status = 'completed'; await tick();
    expect(screen.getByRole('table', { name: 'Выбранные кампании' })).toBeInTheDocument();
  });

  it('дочитывает все страницы финального журнала и не теряет результаты при сбое журнала', async () => {
    status = 'completed';
    override = path => {
      if (path.endsWith('events/?after=0')) return json({ count: 2, next: '?after=1', previous: null, results: [event] });
      if (path.endsWith('events/?after=1')) return json({ error: { message: 'Журнал временно недоступен.' } }, 503);
    };
    open();
    expect(await screen.findByRole('table', { name: 'Выбранные кампании' })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Журнал временно недоступен');
    override = path => path.endsWith('events/?after=1') ? json({ count: 1, next: null, previous: null,
      results: [{ ...event, id: 2, message: 'Финальный план сохранён', kind: 'info', pilot: null }] }) : undefined;
    fireEvent.click(screen.getByRole('button', { name: 'Повторить обновление' }));
    expect(await screen.findByText('Финальный план сохранён')).toBeInTheDocument();
    expect(screen.getAllByText('Проверена гипотеза перехода')).toHaveLength(1);
  });

  it('прерывает запрос и прекращает опрос при уходе со страницы', async () => {
    vi.useFakeTimers(); status = 'running'; open(); await act(async () => {});
    let signal: AbortSignal | null | undefined;
    override = (path, init) => {
      if (path === 'runs/plan-1/') { signal = init?.signal; return new Promise(() => {}); }
      if (path === 'runs/?page=1') return json({ count: 0, next: null, previous: null, results: [] });
    };
    await tick();
    fireEvent.click(screen.getByRole('link', { name: '← Все планы' }));
    await act(async () => {});
    expect(signal?.aborted).toBe(true);
    const count = calls.length; await tick(); expect(calls).toHaveLength(count);
  });

  it('показывает ошибку расчёта и не предлагает запуск того же плана', async () => {
    status = 'failed';
    override = path => path === 'runs/plan-1/' ? json({ ...draft, status, error: { code: 'timeout', message: 'Превышено время расчёта.' } }) : undefined;
    open(); expect(await screen.findByText('Превышено время расчёта.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Запустить расчёт' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Создать новый план' })).toHaveAttribute('href', '/runs/new');
  });

  it('обрабатывает пустой результат и повторяет недоступный результат вручную', async () => {
    status = 'completed';
    override = path => path.endsWith('/results/') ? json({ error: { message: 'Результат ещё сохраняется.' } }, 409) : undefined;
    open(); await screen.findByText(/Результат ещё сохраняется/);
    override = path => path.endsWith('/results/') ? json({ ...results, campaigns: [] }) : undefined;
    fireEvent.click(screen.getByRole('button', { name: 'Повторить обновление' }));
    expect(await screen.findByText('Подходящих кампаний не найдено')).toBeInTheDocument();
  });

  it('скачивает CSV сервера и показывает ошибку экспорта', async () => {
    status = 'completed';
    override = path => path.endsWith('/export/') ? json({ error: { message: 'Экспорт недоступен.' } }, 403) : undefined;
    open();
    fireEvent.click(await screen.findByRole('button', { name: 'Скачать CSV' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Экспорт недоступен.');
    const create = vi.fn((_blob: Blob) => 'blob:test'); const revoke = vi.fn();
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    override = path => path.endsWith('/export/') ? new Response('campaign_name,channel\nТест,sms', { headers: { 'Content-Type': 'text/csv' } }) : undefined;
    fireEvent.click(screen.getByRole('button', { name: 'Скачать CSV' }));
    await waitFor(() => expect(click).toHaveBeenCalledOnce());
    expect(create.mock.calls[0][0]).toBeInstanceOf(Blob);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument(); click.mockRestore();
  });

  it('объясняет отсутствие данных и не даёт создать план', async () => {
    override = path => path === 'datasets/current/' ? json({ error: { message: 'Нет набора' } }, 404) : undefined;
    open('/runs/new');
    expect(await screen.findByText(/Сначала импортируйте/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Сохранить план/ })).not.toBeInTheDocument();
  });
});
