import { useEffect, useState } from 'react';
import { NavLink, useParams } from 'react-router-dom';
import { Download, Play, RefreshCw, Square } from 'lucide-react';
import { useRun } from '../state/RunContext';
import type { ActorId, CommandType, JsonValue, Meta, Run, RunEvent, RunResult, TeamArtifact, TeamTask } from '../api/types';
import './RunDetail.css';

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

const actorLabels: Record<ActorId, string> = {
  lead: 'Бек · руководитель', analyst: 'Дана · аналитик', finance: 'Дана · финансист',
  experiment: 'Айя · экспериментатор', control: 'Жан · контролёр',
};
const taskLabels: Record<TeamTask['status'], string> = {
  pending: 'Ожидает', running: 'В работе', completed: 'Завершено', failed: 'Ошибка', cancelled: 'Отменено',
};
const channels = ['push', 'sms', 'digital_ads', 'call'] as const;

function jsonDisplay(value: unknown) {
  return value == null ? 'Нет данных' : JSON.stringify(value, null, 2);
}

function TeamView({ petHomeRef }: { petHomeRef?: (node: HTMLDivElement | null) => void }) {
  const { run, team, teamUnavailable, events, command, commandError, sendCommand } = useRun();
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [campaignId, setCampaignId] = useState('');
  const [planName, setPlanName] = useState('');
  const [budget, setBudget] = useState('');
  const [allowedChannels, setAllowedChannels] = useState<string[]>([]);
  const [commandType, setCommandType] = useState<CommandType>('explain');
  const [formError, setFormError] = useState('');
  const tasks = team?.tasks || [];
  const artifacts = team?.artifacts || [];
  const activeTask = tasks.find(task => task.id === selectedTask) || null;
  const evidence = activeTask ? artifacts.filter(artifact => activeTask.evidence_ids.includes(artifact.id)) : [];
  const taskFailure = activeTask ? [...events].reverse().find(event => event.kind === 'task_failed' && event.payload.task_id === activeTask.id) : null;
  const handoffs = events.filter(event => event.kind === 'task_handoff');
  const available = team?.available_commands || [];
  const constraints = () => ({ ...(budget.trim() ? { budget: budget.trim() } : {}),
    ...(allowedChannels.length ? { allowed_channels: allowedChannels } : {}) });
  async function submit() {
    setFormError('');
    if (!available.includes(commandType)) return;
    if (commandType === 'explain' && !campaignId.trim()) { setFormError('Укажите ID кампании из сохранённого результата.'); return; }
    if (commandType === 'create_plan' && !planName.trim()) { setFormError('Укажите название нового плана.'); return; }
    if (budget.trim() && (!/^\d+(?:\.\d{1,2})?$/.test(budget.trim()) || Number(budget) <= 0 || Number(budget) > 100000)) {
      setFormError('Бюджет должен быть больше 0 и не больше 100000.'); return;
    }
    const parameters = commandType === 'explain' ? { campaign_id: campaignId.trim() }
      : commandType === 'create_plan' ? { name: planName.trim(), constraints: constraints() }
      : { constraints: constraints() };
    await sendCommand(commandType, parameters);
  }
  return <section className="live-team" aria-label="Живая команда">
    <div className="section-heading"><div><h2>Живая команда</h2><p className="subtle">{run ? `План: ${run.name}` : 'Выберите план, чтобы открыть команду.'}</p></div>
      <span className="badge">{teamUnavailable ? 'Данные команды пока недоступны' : team ? `${tasks.length} задач` : 'Загружаем команду'}</span></div>
    <div ref={petHomeRef} className="pet-office-home" />
    {teamUnavailable && <div className="notice">Сервер пока не отдаёт задачи и команды для этого запуска. Расчёт и результаты доступны ниже.</div>}
    {team && <>
      <div className="live-grid"><div className="panel"><h3>Задачи</h3>
        {tasks.length ? <div className="task-list">{tasks.map(task => <button type="button" className={`task-item ${selectedTask === task.id ? 'active' : ''}`} key={task.id} onClick={() => setSelectedTask(task.id)}>
          <span><strong>{task.title}</strong><small>{actorLabels[task.actor_id]} · {taskLabels[task.status]}</small></span><span aria-hidden="true">→</span>
        </button>)}</div> : <p className="subtle">Задач пока нет. Команда ожидает запуска или первого события.</p>}
      </div><div className="panel"><h3>Входные данные и доказательства</h3>
        {!activeTask ? <p className="subtle">Выберите задачу, чтобы посмотреть сохранённый результат.</p> : <>
          <p><strong>{activeTask.title}</strong><br /><span className="subtle">{actorLabels[activeTask.actor_id]} · {taskLabels[activeTask.status]}</span></p>
          {taskFailure && <div className="notice error" role="alert">{String(taskFailure.payload.reason || taskFailure.payload.error || 'Задача завершилась с ошибкой.')}</div>}
          <p className="subtle">Входные данные задачи: {activeTask.evidence_ids.length ? activeTask.evidence_ids.join(', ') : 'не указаны'}</p>
          {evidence.map(artifact => <ArtifactView key={`evidence-${artifact.id}`} artifact={artifact} />)}
          {artifacts.filter(artifact => activeTask.artifact_ids.includes(artifact.id)).map(artifact => <ArtifactView key={artifact.id} artifact={artifact} />)}
          {activeTask.artifact_ids.length === 0 && <p className="subtle">Результат пока не сохранён.</p>}
        </>}
      </div></div>
      <div className="panel handoff-panel"><h3>Передачи между участниками</h3>
        {handoffs.length ? <ol>{handoffs.map(event => { const from = event.payload.from_actor as ActorId; const to = event.payload.to_actor as ActorId;
          return <li key={event.id}><strong>{actorLabels[from] || String(from || 'Участник')} → {actorLabels[to] || String(to || 'Участник')}</strong>
            <span className="subtle"> · {new Date(event.created_at).toLocaleTimeString('ru-RU')}</span>
            {typeof event.payload.to_task_id === 'string' && <button className="handoff-link" onClick={() => setSelectedTask(String(event.payload.to_task_id))}>Открыть задачу</button>}
          </li>; })}</ol> : <p className="subtle">Подтверждённых передач пока нет.</p>}
      </div>
      <div className="panel command-panel"><h3>Обратиться к команде</h3>
        <p className="subtle">Команды используют сохранённые данные этого плана. Доступность определяет сервер.</p>
        <div className="command-tabs" role="group" aria-label="Команда">
          {(['explain', 'compare', 'create_plan'] as CommandType[]).map(type => <button type="button" key={type} className={`button ${commandType === type ? 'primary' : ''}`} disabled={!available.includes(type)} onClick={() => setCommandType(type)}>{type === 'explain' ? 'Объяснить' : type === 'compare' ? 'Сравнить' : 'Создать план'}</button>)}
        </div>
        {available.length === 0 && <p className="subtle">Сейчас доступных команд нет.</p>}
        {commandType === 'explain' ? <label>ID кампании<input value={campaignId} onChange={event => setCampaignId(event.target.value)} placeholder="ID из сохранённого результата" disabled={!available.includes('explain')} /></label> : <>
          {commandType === 'create_plan' && <label>Название нового плана<input value={planName} onChange={event => setPlanName(event.target.value)} disabled={!available.includes('create_plan')} /></label>}
          <label>Бюджет нового ограничения, у. е. (необязательно)<input inputMode="decimal" value={budget} onChange={event => setBudget(event.target.value)} placeholder="Например, 50000" disabled={!available.includes(commandType)} /></label>
          <fieldset><legend>Доступные каналы (необязательно)</legend><div className="channel-options">{channels.map(channel => <label key={channel}><input type="checkbox" checked={allowedChannels.includes(channel)} disabled={!available.includes(commandType)} onChange={event => setAllowedChannels(previous => event.target.checked ? [...previous, channel] : previous.filter(item => item !== channel))} />{channelLabels[channel]}</label>)}</div></fieldset>
        </>}
        {formError && <div className="notice error" role="alert">{formError}</div>}
        {commandError != null && <Failure error={commandError} />}
        <button className="button primary" type="button" disabled={!available.includes(commandType) || command?.status === 'queued' || command?.status === 'running'} onClick={() => void submit()}>Отправить команду</button>
        {command && <div className="command-response" role="status"><strong>Ответ команды · {command.status === 'completed' ? 'готово' : command.status === 'failed' ? 'ошибка' : 'в работе'}</strong>
          {command.error ? <p>{command.error.message}</p> : command.result != null ? <pre className="result-json">{jsonDisplay(command.result)}</pre> : <p className="subtle">Ожидаем ответ сервера.</p>}
          {command.type === 'create_plan' && command.status === 'completed' && typeof command.result === 'object' && command.result && !Array.isArray(command.result) && typeof command.result.run_id === 'string' && <NavLink className="button" to={`/runs/${command.result.run_id}`}>Открыть новый план</NavLink>}
        </div>}
      </div>
    </>}
  </section>;
}

function ArtifactView({ artifact }: { artifact: TeamArtifact }) {
  return <div className="artifact"><strong>{artifact.title}</strong><small>{artifact.type} · {artifact.id}</small>
    <pre className="result-json">{jsonDisplay(artifact.data)}</pre>
    <p className="subtle">Доказательства: {artifact.evidence_ids.length ? artifact.evidence_ids.join(', ') : 'не указаны'}</p>
  </div>;
}

export default function RunDetail({ meta, petHomeRef }: { meta: Meta; petHomeRef?: (node: HTMLDivElement | null) => void }) {
  const { id } = useParams();
  const { selectedId, select, run, events, result, loadError, actionError, action, refresh, perform } = useRun();
  const [mode, setMode] = useState<'desk' | 'live'>('desk');
  useEffect(() => { if (id && selectedId !== id) select(id); }, [id, selectedId, select]);
  const current = selectedId === id ? run : null;
  const pilots = selectedId === id ? events.filter(event => event.kind === 'pilot_completed') : [];
  return <>
    <NavLink className="back-link" to="/runs">← Все планы</NavLink>
    <div className="run-mode-switch" role="group" aria-label="Представление запуска"><button type="button" className={`button ${mode === 'desk' ? 'primary' : ''}`} aria-pressed={mode === 'desk'} onClick={() => setMode('desk')}>Рабочий стол</button>
      <button type="button" className={`button ${mode === 'live' ? 'primary' : ''}`} aria-pressed={mode === 'live'} onClick={() => setMode('live')}>Живая команда</button></div>
    {loadError != null && <><Failure error={loadError} /><button className="button" onClick={refresh}><RefreshCw size={16} />Обновить состояние</button></>}
    {!current ? !loadError && <p role="status">Загружаем план…</p> : <>
      <div className="section-heading"><div><h1>{current.name}</h1><p className="subtle run-subtitle">{current.strategy === 'openai' ? 'Гипотезы OpenAI + расчётная стратегия' : 'Расчётная стратегия'} · Seed {current.seed}</p></div>
        <span className={`badge status-${current.status}`}>{statuses[current.status]}</span></div>
      <div className="stats-grid"><article className="stat"><div className="stat-label">Бюджет</div><strong>{format(current.budget)}</strong><small>у. е. · расход {format(current.progress.spent_budget)} · остаток {current.progress.spent_budget === null ? '—' : format(Math.max(0, Number(current.budget) - Number(current.progress.spent_budget)))}</small></article>
        <article className="stat"><div className="stat-label">Контакты</div><strong>{format(current.progress.used_contacts)}</strong><small>Лимит {format(current.max_contacts)} · включая пилоты</small></article>
        <article className="stat"><div className="stat-label">Завершено пилотов</div><strong>{current.progress.completed_pilots} / {current.max_pilots}</strong><small>Агент может закончить раньше лимита</small></article></div>
      {actionError != null && <Failure error={actionError} />}
      {events.some(event => event.kind === 'fallback_used') && <div className="notice">Гипотезы OpenAI недоступны. Агент продолжает работу с расчётными гипотезами.</div>}
      <div className="panel run-control">
        {current.status === 'draft' ? <><div><h2>План готов к запуску</h2><p>{meta.features.run_execution ? 'Агент проверит гипотезы на пилотах и соберёт кампании в пределах бюджета.' : 'Расчёты временно отключены на сервере.'}</p></div>
          <button className="button primary" onClick={() => void perform('start')} disabled={action !== null || !meta.features.run_execution}><Play size={16} />{action === 'start' ? 'Запускаем…' : 'Запустить агента'}</button></> : <>
          <div className="run-progress"><div className="section-heading"><h2>{current.progress.stage === 'finalizing' ? 'Сохраняем результаты' : statuses[current.status]}</h2><span>{current.progress.percent}%</span></div>
            <progress value={current.progress.percent} max={100} aria-label="Прогресс расчёта" />
            <p>{current.status === 'queued' ? 'Ожидаем свободный процесс расчёта.' : current.status === 'running'
              ? current.cancellation_requested ? 'Отмена запрошена. Завершаем текущий шаг.' : 'Проверяем гипотезы. Прогресс обновляется каждые 2 секунды.'
              : current.status === 'cancelled' ? 'Расчёт остановлен. Выполненные пилоты сохранены.' : current.status === 'failed' ? current.error?.message || 'Расчёт завершился с ошибкой.' : 'Кампании и результаты сохранены.'}</p>
          </div>
          {['queued', 'running'].includes(current.status) && <button className="button" onClick={() => void perform('cancel')} disabled={action !== null || current.cancellation_requested}><Square size={15} />{action === 'cancel' ? 'Отменяем…' : current.cancellation_requested ? 'Ожидаем остановки' : 'Отменить расчёт'}</button>}
        </>}
      </div>
      {mode === 'live' && <TeamView petHomeRef={petHomeRef} />}
      {result && <Results result={result} canExport={meta.features.csv_export} exporting={action === 'export'} onExport={() => void perform('export')} />}
      {current.status !== 'draft' && <section className="pilot-section"><div className="section-heading"><h2>Журнал пилотов</h2><span className="subtle">{pilots.length} подтверждено</span></div>
        {pilots.length === 0 ? <div className="panel"><p>Завершённых пилотов пока нет.</p></div> : <div className="table-wrap"><table><thead><tr><th>№</th><th>Гипотеза</th><th>Канал</th><th>Запрошено</th><th>Контакты</th><th>Стоимость, у. е.</th><th>Наблюдаемый эффект</th><th>Время</th></tr></thead><tbody>
          {pilots.map(event => <PilotRow key={event.id} event={event} />)}
        </tbody></table></div>}
      </section>}
    </>}
  </>;
}
