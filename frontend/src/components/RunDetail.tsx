import { useEffect, useState } from 'react';
import { NavLink, useParams } from 'react-router-dom';
import { Check, FlaskConical, Play, RefreshCw, Square } from 'lucide-react';
import { useRun } from '../state/RunContext';
import { parseTeamRequest } from '../api/team-requests';
import type { ActorId, CommandType, Meta, Run, TeamArtifact, TeamTask } from '../api/types';
import { channelLabels, formatNumber, isActive, statusLabels } from '../runs/presentation';
import RunResults from '../runs/RunResults';
import '../runs/runs.css';
import './RunDetail.css';

function Stepper({ status }: { status: Run['status'] }) {
  const current = status === 'draft' ? 0 : status === 'completed' ? 2 : 1;
  return <ol className="run-steps" aria-label="Этапы плана">{['План подготовлен', 'Расчёт и пилоты', 'Результаты'].map((label, index) =>
    <li key={label} className={index < current ? 'done' : index === current ? 'current' : ''} aria-current={index === current ? 'step' : undefined}>
      <span aria-hidden="true">{index < current ? <Check size={14} /> : index + 1}</span>{label}
    </li>)}</ol>;
}

function Failure({ error }: { error: unknown }) {
  return <div className="notice error" role="alert">{error instanceof Error ? error.message
    : 'Не удалось получить данные. Повторите запрос.'}</div>;
}

const actorLabels: Record<ActorId, string> = {
  lead: 'Бек · руководитель', analyst: 'Дана · аналитик', finance: 'Дана · финансист',
  experiment: 'Айя · экспериментатор', control: 'Жан · контролёр',
};
const taskLabels: Record<TeamTask['status'], string> = {
  pending: 'Ожидает', running: 'В работе', completed: 'Завершено', failed: 'Ошибка', cancelled: 'Отменено',
};
const channels = ['push', 'sms', 'digital_ads', 'call'] as const;
const commandChannelLabels: Record<string, string> = channelLabels;

function jsonDisplay(value: unknown) {
  return value == null ? 'Нет данных' : JSON.stringify(value, null, 2);
}

function TeamView() {
  const { run, team, teamUnavailable, events, command, commandError, sendCommand } = useRun();
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [campaignId, setCampaignId] = useState('');
  const [planName, setPlanName] = useState('');
  const [budget, setBudget] = useState('');
  const [allowedChannels, setAllowedChannels] = useState<string[]>([]);
  const [commandType, setCommandType] = useState<CommandType>('explain');
  const [formError, setFormError] = useState('');
  const [requestText, setRequestText] = useState('');
  const tasks = team?.tasks || [];
  const artifacts = team?.artifacts || [];
  const activeTask = tasks.find(task => task.id === selectedTask) || null;
  const evidence = activeTask ? artifacts.filter(artifact => activeTask.evidence_ids.includes(artifact.id)) : [];
  const taskFailure = activeTask ? [...events].reverse().find(event => event.event_kind === 'task_failed' && event.payload.task_id === activeTask.id) : null;
  const handoffs = events.filter(event => event.event_kind === 'task_handoff');
  const available = team?.available_commands || [];
  const constraints = () => ({ ...(budget.trim() ? { budget: budget.trim() } : {}),
    ...(allowedChannels.length ? { allowed_channels: allowedChannels } : {}) });
  function interpretRequest() {
    setFormError('');
    const intent = parseTeamRequest(requestText);
    if (!intent) { setFormError('Напишите: «объясни ID», «сравни бюджет 50000» или «создай план Название».'); return; }
    if (!available.includes(intent.type)) { setFormError('Эта команда сейчас недоступна по состоянию сервера.'); return; }
    setCommandType(intent.type);
    if (intent.campaignId) setCampaignId(intent.campaignId);
    if (intent.budget) setBudget(intent.budget);
    if (intent.planName) setPlanName(intent.planName);
  }
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
        <label>Напишите команде<input value={requestText} onChange={event => setRequestText(event.target.value)}
          placeholder="Например, сравни бюджет 50000" disabled={available.length === 0} /></label>
        <button className="button" type="button" onClick={interpretRequest} disabled={available.length === 0}>Подготовить команду</button>
        <p className="subtle">Текст заполнит поля ниже. Отправка произойдёт только после нажатия «Отправить команду».</p>
        <div className="command-tabs" role="group" aria-label="Команда">
          {(['explain', 'compare', 'create_plan'] as CommandType[]).map(type => <button type="button" key={type} className={`button ${commandType === type ? 'primary' : ''}`} disabled={!available.includes(type)} onClick={() => setCommandType(type)}>{type === 'explain' ? 'Объяснить' : type === 'compare' ? 'Сравнить' : 'Создать план'}</button>)}
        </div>
        {available.length === 0 && <p className="subtle">Сейчас доступных команд нет.</p>}
        {commandType === 'explain' ? <label>ID кампании<input value={campaignId} onChange={event => setCampaignId(event.target.value)} placeholder="ID из сохранённого результата" disabled={!available.includes('explain')} /></label> : <>
          {commandType === 'create_plan' && <label>Название нового плана<input value={planName} onChange={event => setPlanName(event.target.value)} disabled={!available.includes('create_plan')} /></label>}
          <label>Бюджет нового ограничения, у. е. (необязательно)<input inputMode="decimal" value={budget} onChange={event => setBudget(event.target.value)} placeholder="Например, 50000" disabled={!available.includes(commandType)} /></label>
          <fieldset><legend>Доступные каналы (необязательно)</legend><div className="channel-options">{channels.map(channel => <label key={channel}><input type="checkbox" checked={allowedChannels.includes(channel)} disabled={!available.includes(commandType)} onChange={event => setAllowedChannels(previous => event.target.checked ? [...previous, channel] : previous.filter(item => item !== channel))} />{commandChannelLabels[channel]}</label>)}</div></fieldset>
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
  const [confirmCancel, setConfirmCancel] = useState(false);
  useEffect(() => { if (id && selectedId !== id) select(id); }, [id, selectedId, select]);
  const current = selectedId === id ? run : null;
  const currentEvents = selectedId === id ? events : [];
  const currentResult = selectedId === id ? result : null;
  const active = current ? isActive(current) : false;
  const cancelled = Boolean(current?.cancellation_requested);
  const usage = currentResult?.totals ?? current?.progress;
  const spent = usage && 'spent' in usage ? usage.spent : null;
  const contactsUsed = usage?.contacts_used;
  const percent = current?.progress?.percent;
  const metrics = current ? [
    { label: 'Остаток бюджета', value: spent != null ? formatNumber(Number(current.budget) - Number(spent)) : '—', note: `из ${formatNumber(current.budget)} у. е.`, used: spent != null ? `${formatNumber(spent)} у. е. потрачено` : 'Расходы ещё не получены' },
    { label: 'Остаток контактов', value: contactsUsed != null ? formatNumber(current.max_contacts - contactsUsed) : '—', note: `из ${formatNumber(current.max_contacts)}`, used: contactsUsed != null ? `${formatNumber(contactsUsed)} использовано` : 'Данные ещё не получены' },
    { label: 'Завершено пилотов', value: current.progress ? formatNumber(current.progress.pilots_completed) : '—', note: `из ${current.max_pilots}`, used: 'Учитываются в бюджете и контактах' },
  ] : [];

  return <div className="run-detail">
    <NavLink className="back-link" to="/runs">← Все планы</NavLink>
    <div className="run-mode-switch" role="group" aria-label="Представление запуска">
      <button type="button" className={`button ${mode === 'desk' ? 'primary' : ''}`} aria-pressed={mode === 'desk'} onClick={() => setMode('desk')}>Рабочий стол</button>
      <button type="button" className={`button ${mode === 'live' ? 'primary' : ''}`} aria-pressed={mode === 'live'} onClick={() => setMode('live')}>Живая команда</button>
    </div>
    {loadError != null && <><Failure error={loadError} /><p>Показаны последние полученные данные.</p><button className="button" onClick={refresh}><RefreshCw size={15} />Повторить обновление</button></>}
    {!current ? !loadError && <p role="status">Загружаем план…</p> : <>
      <div className="section-heading"><div><h1>{current.name}</h1><p className="run-meta">Создан {new Date(current.created_at).toLocaleString('ru-RU')} · {current.strategy === 'openai' ? 'AI-стратегия' : 'Базовая стратегия'} · Seed {current.seed}</p></div>
        <span className={`badge status-${current.status}`} role="status">{statusLabels[current.status]}</span></div>
      <Stepper status={current.status} />
      <div ref={petHomeRef} className="pet-office-home" />
      {meta.environment && <div className="notice">{meta.environment.label}</div>}
      {actionError != null && <Failure error={actionError} />}
      <section className="panel execution-panel" aria-label="Управление расчётом">
        <div><h2>{current.status === 'draft' ? 'Всё начинается с проверки гипотез' : current.status === 'queued' ? 'Расчёт в очереди' : current.status === 'running' ? 'Агент проверяет кампании' : current.status === 'completed' ? 'Расчёт завершён' : current.status === 'cancelled' ? 'Расчёт остановлен' : 'Расчёт завершился с ошибкой'}</h2>
          <p>{current.status === 'draft' ? 'Агент проведёт пилоты в пределах заданных лимитов и выберет кампании для итогового плана.' : active ? 'Можно уйти со страницы и вернуться позже. Ход расчёта сохраняется.' : current.status === 'completed' ? 'Изучите выбранные кампании и их обоснование перед экспортом.' : 'Выполненные пилоты остаются в журнале. Для нового расчёта создайте новый план.'}</p></div>
        {current.status === 'draft' && <button className="button primary" disabled={!meta.features.run_execution || Boolean(current.execution_blocker) || action !== null} onClick={() => void perform('start')}><Play size={16} />{action === 'start' ? 'Запускаем…' : 'Запустить расчёт'}</button>}
        {active && <button className="button" disabled={action !== null || cancelled} onClick={() => setConfirmCancel(true)}><Square size={14} />{cancelled ? 'Остановка запрошена' : action === 'cancel' ? 'Останавливаем…' : 'Остановить расчёт'}</button>}
        {(current.status === 'failed' || current.status === 'cancelled') && <NavLink className="button" to="/runs/new">Создать новый план</NavLink>}
        {current.status === 'draft' && current.execution_blocker && <div className="notice">{current.execution_blocker}</div>}
        {!meta.features.run_execution && <div className="notice">Расчёты пока недоступны для новых запусков. Сохранённые данные и остановка активных расчётов остаются доступны.</div>}
        {current.error && <div className="notice error" role="alert">{current.error.message}</div>}
        {active && <div className="run-progress"><div><span>{cancelled ? 'Ожидаем подтверждение остановки' : current.progress?.stage || 'Ожидаем данные о ходе расчёта'}</span><span>{percent != null ? `${formatNumber(percent)}%` : 'В процессе'}</span></div>
          <div className="progress-track" role="progressbar" aria-label="Прогресс расчёта" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? undefined} aria-valuetext={percent == null ? 'Ожидаем данные' : undefined}>
            {percent != null && <span style={{ width: `${Math.max(0, Math.min(100, percent))}%` }} />}</div></div>}
        {confirmCancel && active && !cancelled && <div className="cancel-confirm" role="group" aria-label="Подтверждение остановки">
          <p>Остановить расчёт? Уже выполненные пилоты и их расходы сохранятся.</p>
          <button className="button danger" disabled={action !== null} onClick={() => { void perform('cancel'); setConfirmCancel(false); }}>Подтвердить остановку</button>
          <button className="button" onClick={() => setConfirmCancel(false)}>Продолжить расчёт</button></div>}
      </section>
      <div className="stats-grid run-resources">{metrics.map(metric => <article className="stat" key={metric.label} aria-label={metric.label}>
        <div className="stat-label">{metric.label}</div><strong>{metric.value}</strong><span className="resource-limit">{metric.note}</span><small>{metric.used}</small>
      </article>)}</div>
      {mode === 'live' && <TeamView />}
      {current.status === 'completed' && <RunResults results={currentResult} exporting={action === 'export'} canExport={meta.features.csv_export} onExport={() => void perform('export')} />}
      <section className="run-journal" aria-labelledby="journal-heading"><div className="section-heading"><h2 id="journal-heading">Журнал пилотов и событий</h2><span className="subtle">{currentEvents.length} событий</span></div>
        {currentEvents.length === 0 ? <div className="empty journal-empty"><FlaskConical size={25} /><h3>Событий пока нет</h3><p>{current.status === 'draft' ? 'После запуска здесь появятся проверенные гипотезы и результаты пилотов.' : 'События появятся после получения журнала с сервера.'}</p></div> :
          <ol className="event-list">{currentEvents.map(event => <li key={event.id} className={`event-${event.kind}`}>
            <time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString('ru-RU')}</time><div><strong>{event.message}</strong>
              {event.pilot && <div className="pilot-details"><p>{event.pilot.campaign_name} · {event.pilot.target_tariff} · {channelLabels[event.pilot.channel]}</p>
                <dl><div><dt>Клиенты</dt><dd>{formatNumber(event.pilot.customers)}</dd></div><div><dt>Расходы</dt><dd>{formatNumber(event.pilot.cost)} у. е.</dd></div><div><dt>Наблюдаемый прирост ARPU</dt><dd>{event.pilot.observed_lift_ratio == null ? 'Нет данных' : `${formatNumber(event.pilot.observed_lift_ratio * 100)}%`}</dd></div></dl></div>}</div>
          </li>)}</ol>}
      </section>
      {mode === 'desk' && <TeamView />}
    </>}
  </div>;
}
