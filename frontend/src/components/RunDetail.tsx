import { useEffect, useRef, useState } from 'react';
import { NavLink, useParams } from 'react-router-dom';
import { Download, Play, RefreshCw, Square } from 'lucide-react';
import { api } from '../api/client';
import { mergeEvents, nextEventCursor, startKeyForRun } from '../api/run-state';
import type { JsonValue, Meta, Run, RunEvent, RunResult } from '../api/types';

const format = (value: unknown) => typeof value === 'number' || typeof value === 'string'
  ? (Number.isFinite(Number(value)) ? new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 })
    .format(Number(value)) : '—') : '—';
const statuses: Record<Run['status'], string> = {
  draft: 'Черновик', queued: 'В очереди', running: 'Выполняется', completed: 'Завершён',
  failed: 'Ошибка', cancelled: 'Отменён',
};
const channelLabels: Record<string, string> = { push: 'Push', sms: 'SMS', digital_ads: 'Реклама', call: 'Звонок' };
const objectValue = (value: unknown): Record<string, unknown> => value && typeof value === 'object'
  && !Array.isArray(value) ? value as Record<string, unknown> : {};
const warningLabels: Record<string, string> = {
  'history_unavailable: using broad priors and public catalogues': 'История переходов недоступна. Использованы текущие данные и общие начальные оценки.',
  'Pilot IDs unknown: membership is averaged analytically': 'Состав пилотных выборок неизвестен; пересечения аудиторий оценены приближённо.',
  'Tail reflects effect scenarios, not full membership risk': 'Нижние 10% модельных исходов отражают неопределённость эффекта, но не весь риск случайного состава пилотов.',
  'Unpiloted call uses a conservative saturation bound': 'Эффект звонков без пилота оценён консервативно.',
  'Campaign costs are incomplete; total spend is unknown.': 'Стоимость части кампаний неизвестна; общие расходы не рассчитаны.',
  'Campaign contact counts are incomplete; total contacts are unknown.': 'Для части кампаний неизвестен охват; общее число контактов не рассчитано.',
  'Explanations are missing for some campaigns.': 'Для части кампаний не сохранены объяснения.',
  'Predicted effect was not calculated.': 'Прогноз эффекта не рассчитан.',
  'Simulator result was not calculated.': 'Результат симулятора не рассчитан.',
};
const warningText = (warning: string) => warning.startsWith('hypothesis_fallback:')
  ? 'Гипотезы OpenAI недоступны; использован расчётный поиск.' : warningLabels[warning] || warning;

function PilotRow({ event }: { event: RunEvent }) {
  const request = objectValue(event.payload.request);
  const observation = objectValue(event.payload.observation);
  const audience = [request.filter_current_tariff, request.filter_arpu_segment].filter(Boolean).join(' / ');
  return <tr><td>{format(event.payload.sequence)}</td>
    <td className="wrap-cell">{request.target_tariff ? `${audience || 'Вся аудитория'} → ${request.target_tariff}` : '—'}</td>
    <td>{channelLabels[String(event.payload.channel)] || String(event.payload.channel || '—')}</td>
    <td>{format(event.payload.requested_customers)}</td><td>{format(event.payload.n_customers)}</td><td>{format(event.payload.cost)}</td>
    <td>{typeof observation.observed_lift_ratio === 'number' ? `${format(observation.observed_lift_ratio * 100)}%` : '—'}</td>
    <td>{new Date(event.created_at).toLocaleTimeString('ru-RU')}</td></tr>;
}

function Failure({ error }: { error: unknown }) {
  return <div className="notice error" role="alert">{error instanceof Error ? error.message
    : 'Не удалось получить данные. Повторите запрос.'}</div>;
}

function Effect({ value, forecast }: { value: JsonValue; forecast?: boolean }) {
  if (value === null) return <p className="subtle">{forecast ? 'Прогноз не сохранён.'
    : 'Результат симулятора не рассчитан.'}</p>;
  if (typeof value === 'object' && !Array.isArray(value) && forecast
    && typeof value.net_arpu_gain_mean === 'number') return <>
    <strong className="effect-value">{format(value.net_arpu_gain_mean)} <small>у. е.</small></strong>
    {typeof value.lower_tail_mean_10 === 'number' && <p className="subtle">Среднее в нижних 10% модельных исходов: {format(value.lower_tail_mean_10)} у. е.</p>}
    <p className="subtle">Модельная оценка после пилотов. Фактический эффект может отличаться.</p>
  </>;
  if (typeof value === 'string' || typeof value === 'number') return <strong className="effect-value">{format(value)} <small>у. е.</small></strong>;
  return <pre className="result-json">{JSON.stringify(value, null, 2)}</pre>;
}

function Results({ result, canExport, onExport, exporting }: {
  result: RunResult; canExport: boolean; onExport: () => void; exporting: boolean;
}) {
  return <>
    <div className="section-heading"><h2>Выбранные кампании</h2>{canExport && <button className="button" onClick={onExport} disabled={exporting}>
      <Download size={16} />{exporting ? 'Скачиваем…' : 'Скачать CSV'}</button>}</div>
    <div className="table-wrap"><table><thead><tr><th>Кампания</th><th>Аудитория</th><th>Тариф</th><th>Канал</th><th>Контакты</th><th>Стоимость, у. е.</th></tr></thead><tbody>
      {result.campaigns.map(({ rank, parameters: p, metrics, explanation }) => <tr key={rank}>
        <td className="wrap-cell"><strong>{p.campaign_name}</strong>{explanation && <small className="campaign-explanation">{explanation}</small>}</td>
        <td className="wrap-cell">{[p.filter_current_tariff, p.filter_arpu_segment && `ARPU: ${p.filter_arpu_segment}`,
          p.filter_data_segment && `Интернет: ${p.filter_data_segment}`, p.filter_call_segment && `Звонки: ${p.filter_call_segment}`].filter(Boolean).join(' · ') || 'Вся аудитория'}</td>
        <td>{p.target_tariff}</td><td>{channelLabels[p.channel] || p.channel}</td><td>{format(metrics.n_contacts)}</td><td>{format(metrics.cost)}</td>
      </tr>)}
    </tbody></table></div>
    <div className="result-grid"><article className="panel"><h3>Прогноз чистого эффекта</h3><Effect value={result.totals.predicted_effect} forecast /></article>
      <article className="panel"><h3>Результат симулятора</h3><Effect value={result.totals.simulator_result} /></article></div>
    <div className="panel run-totals"><h3>Общие ресурсы</h3><dl>
      <div><dt>Стоимость пилотов</dt><dd>{format(result.totals.pilot_cost)} у. е.</dd></div>
      <div><dt>Стоимость кампаний</dt><dd>{format(result.totals.campaign_cost)} у. е.</dd></div>
      <div><dt>Всего расходов</dt><dd>{format(result.totals.total_cost)} у. е.</dd></div>
      <div><dt>Контакты с учётом пилотов</dt><dd>{format(result.totals.total_contacts)}</dd></div>
    </dl></div>
    {result.warnings.length > 0 && <div className="notice"><strong>Примечания к расчёту</strong><ul>{result.warnings.map((warning, index) => <li key={index}>{warningText(warning)}</li>)}</ul></div>}
  </>;
}

function RunWorkspace({ id, meta }: { id: string; meta: Meta }) {
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [result, setResult] = useState<RunResult | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [action, setAction] = useState<'start' | 'cancel' | 'export' | null>(null);
  const [refresh, setRefresh] = useState(0);
  const cursor = useRef(0);
  const resultLoaded = useRef(false);
  const actionPending = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setLoadError(null);
    async function poll() {
      try {
        const current = await api.run(id, controller.signal);
        if (!alive) return;
        setRun(current);
        let more = false;
        for (let pageNumber = 0; pageNumber < 5; pageNumber += 1) {
          const page = await api.events(id, cursor.current, controller.signal);
          if (!alive) return;
          cursor.current = nextEventCursor(cursor.current, page);
          setEvents(previous => mergeEvents(previous, page.results));
          more = page.has_more;
          if (!more) break;
        }
        if (current.status === 'completed' && !resultLoaded.current) {
          const output = await api.results(id, controller.signal);
          if (!alive) return;
          resultLoaded.current = true;
          setResult(output);
        }
        if (alive && (more || current.status === 'queued' || current.status === 'running')) {
          timer = setTimeout(poll, 2000);
        }
      } catch (error) { if (alive && !controller.signal.aborted) setLoadError(error); }
    }
    void poll();
    return () => { alive = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [id, refresh]);

  async function perform(kind: 'start' | 'cancel' | 'export') {
    if (actionPending.current) return;
    actionPending.current = true;
    setAction(kind); setActionError(null);
    try {
      if (kind === 'export') {
        const blob = await api.exportRun(id);
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url; anchor.download = `campaigns-${id}.csv`;
        document.body.appendChild(anchor); anchor.click(); anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } else {
        const current = kind === 'start' ? await api.startRun(id, startKeyForRun(id)) : await api.cancelRun(id);
        setRun(current);
      }
    } catch (error) { setActionError(error); }
    finally {
      actionPending.current = false; setAction(null);
      if (kind !== 'export') setRefresh(value => value + 1);
    }
  }

  const pilots = events.filter(event => event.kind === 'pilot_completed');
  return <>
    <NavLink className="back-link" to="/runs">← Все планы</NavLink>
    {loadError !== null && <><Failure error={loadError} /><button className="button" onClick={() => setRefresh(value => value + 1)}><RefreshCw size={16} />Обновить состояние</button></>}
    {!run ? !loadError && <p role="status">Загружаем план…</p> : <>
      <div className="section-heading"><div><h1>{run.name}</h1><p className="subtle run-subtitle">{run.strategy === 'openai' ? 'Гипотезы OpenAI + расчётная стратегия' : 'Расчётная стратегия'} · Seed {run.seed}</p></div>
        <span className={`badge status-${run.status}`}>{statuses[run.status]}</span></div>
      <div className="stats-grid"><article className="stat"><div className="stat-label">Бюджет</div><strong>{format(run.budget)}</strong><small>у. е. · остаток {run.progress.spent_budget === null ? '—' : format(Math.max(0, Number(run.budget) - Number(run.progress.spent_budget)))}</small></article>
        <article className="stat"><div className="stat-label">Контакты</div><strong>{format(run.progress.used_contacts)}</strong><small>Лимит {format(run.max_contacts)} · включая пилоты</small></article>
        <article className="stat"><div className="stat-label">Завершено пилотов</div><strong>{run.progress.completed_pilots} / {run.max_pilots}</strong><small>Агент может закончить раньше лимита</small></article></div>
      {actionError !== null && <Failure error={actionError} />}
      {events.some(event => event.kind === 'fallback_used') && <div className="notice">Гипотезы OpenAI недоступны. Агент продолжает работу с расчётными гипотезами.</div>}
      <div className="panel run-control">
        {run.status === 'draft' ? <><div><h2>План готов к запуску</h2><p>{meta.features.run_execution ? 'Агент проверит гипотезы на пилотах и соберёт кампании в пределах бюджета.' : 'Расчёты временно отключены на сервере.'}</p></div>
          <button className="button primary" onClick={() => void perform('start')} disabled={action !== null || !meta.features.run_execution}><Play size={16} />{action === 'start' ? 'Запускаем…' : 'Запустить агента'}</button></> : <>
          <div className="run-progress"><div className="section-heading"><h2>{run.progress.stage === 'finalizing' ? 'Сохраняем результаты' : statuses[run.status]}</h2><span>{run.progress.percent}%</span></div>
            <progress value={run.progress.percent} max={100} aria-label="Прогресс расчёта" />
            <p>{run.status === 'queued' ? 'Ожидаем свободный процесс расчёта.' : run.status === 'running'
              ? run.cancellation_requested ? 'Отмена запрошена. Завершаем текущий шаг.' : 'Проверяем гипотезы. Прогресс обновляется каждые 2 секунды.'
              : run.status === 'cancelled' ? 'Расчёт остановлен. Выполненные пилоты сохранены.' : run.status === 'failed' ? run.error?.message || 'Расчёт завершился с ошибкой.' : 'Кампании и результаты сохранены.'}</p>
          </div>
          {['queued', 'running'].includes(run.status) && <button className="button" onClick={() => void perform('cancel')} disabled={action !== null || run.cancellation_requested}><Square size={15} />{action === 'cancel' ? 'Отменяем…' : run.cancellation_requested ? 'Ожидаем остановки' : 'Отменить расчёт'}</button>}
        </>}
      </div>
      {result && <Results result={result} canExport={meta.features.csv_export} exporting={action === 'export'} onExport={() => void perform('export')} />}
      {run.status !== 'draft' && <section className="pilot-section"><div className="section-heading"><h2>Журнал пилотов</h2><span className="subtle">{pilots.length} подтверждено</span></div>
        {pilots.length === 0 ? <div className="panel"><p>Завершённых пилотов пока нет.</p></div> : <div className="table-wrap"><table><thead><tr><th>№</th><th>Гипотеза</th><th>Канал</th><th>Запрошено</th><th>Контакты</th><th>Стоимость, у. е.</th><th>Наблюдаемый эффект</th><th>Время</th></tr></thead><tbody>
          {pilots.map(event => <PilotRow key={event.id} event={event} />)}
        </tbody></table></div>}
      </section>}
    </>}
  </>;
}

export default function RunDetail({ meta }: { meta: Meta }) {
  const { id } = useParams();
  return <RunWorkspace key={id} id={id!} meta={meta} />;
}
