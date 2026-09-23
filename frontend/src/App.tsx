import { useEffect, useRef, useState, type FormEvent } from 'react';
import { NavLink, Route, Routes, useNavigate } from 'react-router-dom';
import { ArrowRight, ChartNoAxesCombined, CircleDot, Database, FlaskConical, Layers,
  Plus, RefreshCw, Signal, Wallet, Users, ChevronRight } from 'lucide-react';
import { api, ApiError, onAuthenticationRequired } from './api/client';
import type { Dataset as DatasetType, Meta, Page, Run, Session } from './api/types';
import CountUp from './components/react-bits/CountUp';
import SpotlightCard from './components/react-bits/SpotlightCard';
import RunDetail from './components/RunDetail';
import AgentPets from './components/pets/AgentPets';
import { RunProvider, useRun } from './state/RunContext';

const number = (value: number | string) => new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 0,
}).format(Number(value));
const statusLabels: Record<Run['status'], string> = {
  draft: 'Черновик', queued: 'В очереди', running: 'Выполняется', completed: 'Завершён',
  failed: 'Ошибка', cancelled: 'Отменён',
};

function ErrorMessage({ error }: { error: unknown }) {
  return <div className="notice error" role="alert">{error instanceof Error
    ? error.message : 'Не удалось получить данные.'}</div>;
}

function Overview({ dataset, meta, petHomeRef }: { dataset: DatasetType | null; meta: Meta; petHomeRef: (node: HTMLDivElement | null) => void }) {
  const { selectedId, run } = useRun();
  return <>
    <div className="page-heading"><div><span className="eyebrow">ЦЕНТР УПРАВЛЕНИЯ КАМПАНИЯМИ</span>
      <h1>Каждое решение<br />должно окупаться<span className="yellow-dot">.</span></h1>
      <p>Изучайте аудиторию, проверяйте гипотезы и находите<br className="desktop" /> кампании с наибольшим эффектом.</p>
    </div><div className="hero-symbol" aria-hidden="true"><Signal size={88} strokeWidth={1.4} /></div></div>
    <div className="office-run-heading">{selectedId ? <><div><strong>{run?.name || 'Выбранный план'}</strong><span>{run ? statusLabels[run.status] : 'Загружаем состояние…'}</span></div><NavLink to={`/runs/${selectedId}`}>Открыть рабочую панель<ArrowRight size={15} /></NavLink></>
      : <><div><strong>Команда ждёт план</strong><span>Выберите сохранённый план или создайте новый.</span></div><div><NavLink to="/runs">Выбрать план</NavLink><NavLink to="/runs/new">Создать план</NavLink></div></>}</div>
    <div ref={petHomeRef} className="pet-office-home" />
    <div className="stats-grid">
      <Stat icon={<Users size={18} />} label="Абоненты в базе" value={dataset ? <CountUp to={dataset.customer_count} /> : '—'} note={dataset ? 'Пакет участника импортирован' : 'Ожидается импорт данных'} />
      <Stat icon={<Wallet size={18} />} label="Бюджет кампаний" value={number(meta.limits.budget)} note="у. е. · включая пилоты" />
      <Stat icon={<FlaskConical size={18} />} label="Пилотные проверки" value={<>до <CountUp to={meta.limits.pilots} /></>} note="Проверяйте идеи на малой выборке" />
    </div>
    <div className="section-heading"><h2>От данных к решению</h2><span className="subtle">Три шага к плану кампаний</span></div>
    <div className="workflow-grid">
      {[
        ['01', 'Изучить аудиторию', 'Тарифы, потребление и выручка помогут найти первые гипотезы.', '/data', 'Открыть данные'],
        ['02', 'Подготовить эксперимент', 'Задайте бюджет и лимиты для проверки предложений.', '/runs/new', 'Создать план'],
        ['03', 'Сравнить результаты', 'Сохранённые планы и результаты расчётов в одном месте.', '/runs', 'К списку планов'],
      ].map(([n, title, text, path, link]) => <SpotlightCard className="workflow-card" key={n}>
        <span className="step-number">{n}</span><h3>{title}</h3><p>{text}</p>
        <NavLink to={path}>{link}<ArrowRight size={16} /></NavLink>
      </SpotlightCard>)}
    </div>
    <div className="footnote"><CircleDot size={16} /> Данные хакатона синтетические. Все эксперименты проводятся в симуляторе.</div>
  </>;
}

function Stat({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: React.ReactNode; note: string }) {
  return <article className="stat"><div className="stat-label">{label}{icon}</div><strong>{value}</strong><small>{note}</small></article>;
}

function DataPage({ dataset }: { dataset: DatasetType | null }) {
  return <><div className="section-heading"><div><span className="eyebrow">АУДИТОРИЯ</span><h1>Данные для решений</h1></div></div>
    {!dataset ? <div className="empty"><Database size={36} /><h2>Набор данных ещё не загружен</h2>
      <p>После импорта пакета Beeline здесь появятся состав аудитории и распределение сегментов.</p></div>
      : <><div className="stats-grid"><Stat icon={<Users size={18} />} label="Абоненты" value={<CountUp to={dataset.customer_count} />} note="Уникальные записи в наборе" />
        <Stat icon={<Layers size={18} />} label="Тарифы" value={<CountUp to={dataset.summary.tariff_count} />} note="Из справочника организаторов" />
        <Stat icon={<ChartNoAxesCombined size={18} />} label="Базовая выручка" value={dataset.summary.baseline_arpu === null ? '—' : number(dataset.summary.baseline_arpu)} note="у. е. · прогноз без кампаний" /></div>
        <div className="workflow-grid">{Object.entries(dataset.summary.segments).map(([key, counts]) => <article className="panel" key={key}>
          <h3>{{ arpu_segment: 'Расходы клиентов', data_segment: 'Интернет', call_segment: 'Звонки' }[key]}</h3>
          {Object.entries(counts).map(([label, count]) => <div className="segment" key={label}>
            <div><span>{label}</span><strong>{number(count)}</strong></div>
            <div className="bar"><span style={{ width: `${count / dataset.customer_count * 100}%` }} /></div>
          </div>)}
        </article>)}</div><p className="subtle">Импортирован {new Date(dataset.imported_at).toLocaleString('ru-RU')} · {dataset.name}</p></>}
  </>;
}

function RunsPage() {
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<Page<Run> | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { let active = true; setResult(null); setError(null);
    api.runs(page).then(value => { if (active) setResult(value); }).catch(e => { if (active) setError(e); });
    return () => { active = false; };
  }, [page]);
  return <><div className="section-heading"><div><span className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</span><h1>Планы кампаний</h1></div>
    <NavLink className="button primary" to="/runs/new"><Plus size={18} /> Новый план</NavLink></div>
    {error ? <ErrorMessage error={error} /> : !result ? <p role="status">Загружаем планы…</p> : result.count === 0 ?
      <div className="empty"><FlaskConical size={36} /><h2>Начните с первого плана</h2><p>Задайте бюджет и ограничения, чтобы подготовить эксперимент.</p><NavLink className="button primary" to="/runs/new">Создать план<ArrowRight size={16} /></NavLink></div> :
      <><div className="table-wrap"><table><thead><tr><th>Название</th><th>Статус</th><th>Бюджет</th><th>Пилоты</th><th>Создан</th></tr></thead><tbody>
        {result.results.map(run => <tr key={run.id}><td><NavLink to={`/runs/${run.id}`}>{run.name}<ChevronRight size={15} /></NavLink></td><td><span className="badge">{statusLabels[run.status]}</span></td><td>{number(run.budget)} у. е.</td><td>до {run.max_pilots}</td><td>{new Date(run.created_at).toLocaleDateString('ru-RU')}</td></tr>)}
      </tbody></table></div><div className="pagination"><button disabled={!result.previous} onClick={() => setPage(p => p - 1)}>Назад</button><span>Страница {page} · всего {result.count}</span><button disabled={!result.next} onClick={() => setPage(p => p + 1)}>Далее</button></div></>}
  </>;
}

function NewRun({ dataset, meta }: { dataset: DatasetType | null; meta: Meta }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const pending = useRef(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(null);
    const values = new FormData(event.currentTarget);
    try {
      const run = await api.createRun({ name: String(values.get('name')), budget: String(values.get('budget')),
        max_contacts: Number(values.get('contacts')), max_pilots: Number(values.get('pilots')),
        seed: Number(values.get('seed')), strategy: meta.features.openai_strategy && values.get('strategy') === 'openai' ? 'openai' : 'baseline' });
      navigate(`/runs/${run.id}`);
    } catch (e) { setError(e); } finally { pending.current = false; setBusy(false); }
  }
  return <><span className="eyebrow">НОВЫЙ ЭКСПЕРИМЕНТ</span><h1>Подготовьте план</h1><p className="intro">Определите ресурсы, которые агент сможет использовать для поиска кампаний.</p>
    {!dataset ? <div className="notice">Сначала импортируйте набор данных Beeline.</div> : <form className="panel run-form" onSubmit={submit}>
      <label>Название плана<input name="name" placeholder="Например, кампания на октябрь" maxLength={120} required /></label>
      <div className="form-grid"><label>Бюджет, у. е.<input name="budget" type="number" step="0.01" min="0.01" max={meta.limits.budget} defaultValue={meta.limits.budget} required /></label>
      <label>Максимум контактов<input name="contacts" type="number" min="1" max={meta.limits.contacts} defaultValue={meta.limits.contacts} required /></label></div>
      <label>Количество пилотов<input name="pilots" type="number" min="1" max={meta.limits.pilots} defaultValue={meta.limits.pilots} required /></label>
      {meta.features.openai_strategy && <label>Способ поиска гипотез<select name="strategy" defaultValue="baseline"><option value="baseline">Расчётная стратегия</option><option value="openai">OpenAI + расчётная стратегия</option></select><small className="field-help">OpenAI предлагает гипотезы; пилоты и распределение бюджета рассчитывает движок.</small></label>}
      <label>Seed для повторяемости<input name="seed" type="number" step="1" min="0" max="2147483647" defaultValue="42" required /></label>
      <p className="subtle">Бюджет и контакты включают предварительные эксперименты.</p>
      {error !== null && <><ErrorMessage error={error} />{error instanceof ApiError && Object.keys(error.fields).length > 0 && <ul className="field-errors">{Object.entries(error.fields).map(([key, value]) => <li key={key}>{key}: {String(value)}</li>)}</ul>}</>}
      <button className="button primary" disabled={busy}>{busy ? 'Сохраняем…' : 'Сохранить план'}<ArrowRight size={17} /></button>
    </form>}
  </>;
}

function Login({ onLogin }: { onLogin: (session: Session) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const pending = useRef(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(null);
    const form = event.currentTarget;
    const values = new FormData(form);
    try {
      const session = await api.login(String(values.get('username')), String(values.get('password')));
      form.reset(); onLogin(session);
    } catch (e) { setError(e); } finally { pending.current = false; setBusy(false); }
  }
  return <><span className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</span><h1>Войдите в аккаунт</h1><p className="intro">Используйте учётную запись команды для доступа к данным и запускам агента.</p>
    <form className="panel run-form" onSubmit={submit}><label>Имя пользователя<input name="username" autoComplete="username" maxLength={150} required /></label>
      <label>Пароль<input name="password" type="password" autoComplete="current-password" maxLength={512} required /></label>
      {error !== null && <ErrorMessage error={error} />}<button className="button primary" disabled={busy}>{busy ? 'Входим…' : 'Войти'}<ArrowRight size={16} /></button></form>
  </>;
}

export default function App() {
  const [petHome, setPetHome] = useState<HTMLDivElement | null>(null);
  const [dataset, setDataset] = useState<DatasetType | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);
  const [session, setSession] = useState<Session | null>(null);
  const [needsLogin, setNeedsLogin] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  useEffect(() => onAuthenticationRequired(() => {
    setNeedsLogin(true); setSession(null); setMeta(null); setDataset(null);
  }), []);
  useEffect(() => {
    const controller = new AbortController();
    let active = true; setError(null); setMeta(null);
    async function connect() {
      try {
        const currentSession = await api.session(controller.signal);
        if (!active) return;
        setSession(currentSession);
        const [m, d] = await Promise.all([api.meta(controller.signal), api.dataset(controller.signal).catch(e => {
          if (e instanceof ApiError && e.status === 404) return null;
          throw e;
        })]);
        if (active) { setMeta(m); setDataset(d); setNeedsLogin(false); }
      } catch (e) { if (active && !controller.signal.aborted) setError(e); }
    }
    void connect();
    return () => { active = false; controller.abort(); };
  }, [retry]);
  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true); setError(null);
    try { await api.logout(); try { localStorage.removeItem('redorda:selected-run'); } catch { /* Storage is optional. */ }
      setSession(null); setMeta(null); setDataset(null); setNeedsLogin(true); }
    catch (e) { setError(e); } finally { setLoggingOut(false); }
  }
  return <RunProvider key={session?.user?.id || 'anonymous'} active={!needsLogin && !!meta}><div className="app"><aside className="sidebar"><NavLink className="brand" to="/"><img className="brand-logo" src="/branding/janymda-logo.jpg" alt="" />Janymda</NavLink>
    <div className="workspace-label">BEELINE / HACKALEM AI</div><nav aria-label="Основная навигация">
      <NavLink to="/" end><ChartNoAxesCombined size={19} />Обзор</NavLink>
      <NavLink to="/runs"><FlaskConical size={19} />Планы кампаний</NavLink>
      <NavLink to="/data"><Database size={19} />Аудитория</NavLink>
    </nav><div className="sidebar-bottom"><span className="team-avatar">R</span><div><strong>Команда RedOrda</strong><small>Рабочее пространство</small></div></div></aside>
    <div className="main-shell"><header><span>Маркетинговая аналитика</span><div className="header-account"><div className="connection"><i className={meta ? 'connected' : ''} />{needsLogin ? 'Вход в аккаунт' : meta ? 'Сервис доступен' : error ? 'Нет соединения' : 'Подключение…'}</div>
      {session?.authenticated && <><span className="account-name">{session.user?.username}</span><button className="logout-button" onClick={() => void logout()} disabled={loggingOut}>{loggingOut ? 'Выходим…' : 'Выйти'}</button></>}</div></header>
      <main>{needsLogin ? <Login onLogin={value => { setSession(value); setNeedsLogin(false); setRetry(n => n + 1); }} /> : error ? <><ErrorMessage error={error} /><button className="button" onClick={() => setRetry(n => n + 1)}><RefreshCw size={16} />Повторить</button></> : !meta ? <p role="status">Подключаемся к сервису…</p> : <Routes>
        <Route path="/" element={<Overview dataset={dataset} meta={meta} petHomeRef={setPetHome} />} />
        <Route path="/data" element={<DataPage dataset={dataset} />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/runs/new" element={<NewRun dataset={dataset} meta={meta} />} />
        <Route path="/runs/:id" element={<RunDetail meta={meta} petHomeRef={setPetHome} />} />
        <Route path="*" element={<><h1>Страница не найдена</h1><NavLink to="/">На главную</NavLink></>} />
      </Routes>}</main><footer>RedOrda © 2026 <span>HackAlem AI · Beeline Tariff Marketing Campaigns</span></footer>
    </div><AgentPets home={petHome} /></div></RunProvider>;
}
