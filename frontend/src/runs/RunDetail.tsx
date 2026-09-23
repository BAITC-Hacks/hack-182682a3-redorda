import { useEffect, useRef, useState } from 'react';
import { NavLink, useParams } from 'react-router-dom';
import { Check, FlaskConical, Play, RefreshCw, Square } from 'lucide-react';
import { api } from '../api/client';
import type { Meta, Run } from '../api/types';
import { channelLabels, formatNumber, isActive, statusLabels } from './presentation';
import RunResults from './RunResults';
import { useRun } from './useRun';
import './runs.css';

const startKeys = new Map<string, string>();
function startKey(id: string) {
  const storageKey = `redorda:start:${id}`;
  try {
    const stored = sessionStorage.getItem(storageKey);
    if (stored) return stored;
  } catch { /* A blocked sessionStorage still permits same-page retries. */ }
  const key = startKeys.get(id) ?? crypto.randomUUID();
  startKeys.set(id, key);
  try { sessionStorage.setItem(storageKey, key); } catch { /* In-memory fallback above. */ }
  return key;
}

function Stepper({ status }: { status: Run['status'] }) {
  const current = status === 'draft' ? 0 : status === 'completed' ? 2 : 1;
  return <ol className="run-steps" aria-label="Этапы плана">{['План подготовлен', 'Расчёт и пилоты', 'Результаты'].map((label, index) =>
    <li key={label} className={index < current ? 'done' : index === current ? 'current' : ''} aria-current={index === current ? 'step' : undefined}>
      <span aria-hidden="true">{index < current ? <Check size={14} /> : index + 1}</span>{label}
    </li>)}</ol>;
}

function Detail({ id, meta }: { id: string; meta: Meta }) {
  const { run, events, results, error, loading, retry, accept } = useRun(id, meta.features.run_execution);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'start' | 'cancel' | 'export' | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [cancelRequested, setCancelRequested] = useState(false);
  const lock = useRef(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function action(kind: 'start' | 'cancel' | 'export') {
    if (lock.current) return;
    lock.current = true; setBusy(kind); setActionError(null);
    const controller = new AbortController(); request.current = controller;
    try {
      if (kind === 'export') {
        const blob = await api.exportRun(id, controller.signal);
        if (controller.signal.aborted) return;
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url; link.download = `campaigns-${id}.csv`;
        document.body.appendChild(link); link.click(); link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      } else {
        const value = kind === 'start' ? await api.startRun(id, startKey(id), controller.signal) : await api.cancelRun(id, controller.signal);
        if (controller.signal.aborted) return;
        if (kind === 'cancel') { setCancelRequested(true); setConfirmCancel(false); }
        accept(value);
      }
    } catch (reason) {
      if (controller.signal.aborted) return;
      const message = reason instanceof Error ? reason.message : 'Не удалось выполнить действие.';
      setActionError(kind === 'start' ? `${message} Статус запуска проверяется. Если план остался черновиком, повторный запуск использует тот же ключ.` : message);
      if (kind !== 'export') retry();
    } finally {
      lock.current = false;
      if (!controller.signal.aborted) setBusy(null);
    }
  }

  if (!run) return <><NavLink className="back-link" to="/runs">← Все планы</NavLink>
    {error ? <div className="notice error" role="alert">{error}<div><button className="button" onClick={retry} disabled={loading}>Повторить обновление</button></div></div>
      : <p role="status">Загружаем план…</p>}</>;
  const active = isActive(run);
  const cancelled = cancelRequested || run.cancellation_requested;
  const usage = results?.totals ?? run.progress;
  const percent = run.progress?.percent;
  const metrics = [
    { label: 'Остаток бюджета', value: usage?.spent != null ? formatNumber(Number(run.budget) - Number(usage.spent)) : '—', note: `из ${formatNumber(run.budget)} у. е.`, used: usage?.spent != null ? `${formatNumber(usage.spent)} у. е. потрачено` : 'Расходы ещё не получены' },
    { label: 'Остаток контактов', value: usage?.contacts_used != null ? formatNumber(run.max_contacts - usage.contacts_used) : '—', note: `из ${formatNumber(run.max_contacts)}`, used: usage?.contacts_used != null ? `${formatNumber(usage.contacts_used)} использовано` : 'Данные ещё не получены' },
    { label: 'Завершено пилотов', value: run.progress ? formatNumber(run.progress.pilots_completed) : '—', note: `из ${run.max_pilots}`, used: 'Учитываются в бюджете и контактах' },
  ];

  return <div className="run-detail">
    <NavLink className="back-link" to="/runs">← Все планы</NavLink>
    <div className="section-heading"><div><h1>{run.name}</h1><p className="run-meta">Создан {new Date(run.created_at).toLocaleString('ru-RU')} · {run.strategy === 'openai' ? 'AI-стратегия' : 'Базовая стратегия'}</p></div>
      <span className={`badge status-${run.status}`} role="status">{statusLabels[run.status]}</span></div>
    <Stepper status={run.status} />
    {meta.environment && <div className="notice">{meta.environment.label}</div>}
    {error && <div className="notice error" role="alert">{error}<p>Показаны последние полученные данные.</p><button className="button" onClick={retry} disabled={loading}><RefreshCw size={15} />Повторить обновление</button></div>}
    {actionError && <div className="notice error" role="alert">{actionError}</div>}

    <section className="panel execution-panel" aria-label="Управление расчётом">
      <div><h2>{run.status === 'draft' ? 'Всё начинается с проверки гипотез' : run.status === 'queued' ? 'Расчёт в очереди' : run.status === 'running' ? 'Агент проверяет кампании' : run.status === 'completed' ? 'Расчёт завершён' : run.status === 'cancelled' ? 'Расчёт остановлен' : 'Расчёт завершился с ошибкой'}</h2>
        <p>{run.status === 'draft' ? 'Агент проведёт пилоты в пределах заданных лимитов и выберет кампании для итогового плана.' : active ? 'Можно уйти со страницы и вернуться позже. Ход расчёта сохраняется.' : run.status === 'completed' ? 'Изучите выбранные кампании и их обоснование перед экспортом.' : 'Выполненные пилоты остаются в журнале. Для нового расчёта создайте новый план.'}</p></div>
      {run.status === 'draft' && <button className="button primary" disabled={!meta.features.run_execution || busy !== null || loading} onClick={() => void action('start')}><Play size={16} />{busy === 'start' ? 'Запускаем…' : 'Запустить расчёт'}</button>}
      {active && <button className="button" disabled={!meta.features.run_execution || busy !== null || cancelled} onClick={() => setConfirmCancel(true)}><Square size={14} />{cancelled ? 'Остановка запрошена' : busy === 'cancel' ? 'Останавливаем…' : 'Остановить расчёт'}</button>}
      {(run.status === 'failed' || run.status === 'cancelled') && <NavLink className="button" to="/runs/new">Создать новый план</NavLink>}
      {!meta.features.run_execution && <div className="notice">Расчёты пока недоступны на сервере. План сохранён; запуск появится после подключения расчётов.</div>}
      {run.error && <div className="notice error" role="alert">{run.error.message}</div>}
      {active && <div className="run-progress"><div><span>{cancelled ? 'Ожидаем подтверждение остановки' : run.progress?.stage || 'Ожидаем данные о ходе расчёта'}</span><span>{percent != null ? `${formatNumber(percent)}%` : 'В процессе'}</span></div>
        <div className="progress-track" role="progressbar" aria-label="Прогресс расчёта" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? undefined} aria-valuetext={percent == null ? 'Ожидаем данные' : undefined}>
          {percent != null && <span style={{ width: `${Math.max(0, Math.min(100, percent))}%` }} />}</div></div>}
      {confirmCancel && active && !cancelled && <div className="cancel-confirm" role="group" aria-label="Подтверждение остановки">
        <p>Остановить расчёт? Уже выполненные пилоты и их расходы сохранятся.</p>
        <button className="button danger" disabled={busy !== null} onClick={() => void action('cancel')}>Подтвердить остановку</button>
        <button className="button" onClick={() => setConfirmCancel(false)}>Продолжить расчёт</button></div>}
    </section>

    <div className="stats-grid run-resources">{metrics.map(metric => <article className="stat" key={metric.label} aria-label={metric.label}>
      <div className="stat-label">{metric.label}</div><strong>{metric.value}</strong><span className="resource-limit">{metric.note}</span><small>{metric.used}</small>
    </article>)}</div>

    {run.status === 'completed' && <RunResults results={results} exporting={busy === 'export'} canExport={meta.features.csv_export} onExport={() => void action('export')} />}
    <section className="run-journal" aria-labelledby="journal-heading"><div className="section-heading"><h2 id="journal-heading">Журнал пилотов и событий</h2><span className="subtle">{events.length} событий</span></div>
      {events.length === 0 ? <div className="empty journal-empty"><FlaskConical size={25} /><h3>Событий пока нет</h3><p>{run.status === 'draft' ? 'После запуска здесь появятся проверенные гипотезы и результаты пилотов.' : 'События появятся после получения журнала с сервера.'}</p></div> :
        <ol className="event-list">{events.map(event => <li key={event.id} className={`event-${event.kind}`}>
          <time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString('ru-RU')}</time><div><strong>{event.message}</strong>
            {event.pilot && <div className="pilot-details"><p>{event.pilot.campaign_name} · {event.pilot.target_tariff} · {channelLabels[event.pilot.channel]}</p>
              <dl><div><dt>Клиенты</dt><dd>{formatNumber(event.pilot.customers)}</dd></div><div><dt>Расходы</dt><dd>{formatNumber(event.pilot.cost)} у. е.</dd></div><div><dt>Наблюдаемый прирост ARPU</dt><dd>{event.pilot.observed_lift_ratio == null ? 'Нет данных' : `${formatNumber(event.pilot.observed_lift_ratio * 100)}%`}</dd></div></dl></div>}</div>
        </li>)}</ol>}
    </section>
  </div>;
}

export default function RunDetail({ meta }: { meta: Meta }) {
  const { id } = useParams();
  return <Detail key={id} id={id!} meta={meta} />;
}
