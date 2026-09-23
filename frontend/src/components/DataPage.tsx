import { useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { ArrowRight, Check, CheckCircle2, Circle, FileText, Layers, LoaderCircle,
  ShieldCheck, Sparkles, Upload, Users, Wallet, X } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { Dataset } from '../api/types';
import CountUp from './react-bits/CountUp';
import './DataPage.css';

const REQUIRED_FILES = [
  { name: 'dict_tariff.csv', label: 'Справочник тарифов' },
  { name: 'traffic.csv', label: 'Потребление услуг' },
  { name: 'arpu_monthly.csv', label: 'Выручка по месяцам' },
  { name: 'change_tariff.csv', label: 'История смены тарифов' },
] as const;
const MAX_FILE_SIZE = 30 * 1024 * 1024;
const MAX_TOTAL_SIZE = 50 * 1024 * 1024;
type Phase = 'idle' | 'preparing' | 'uploading' | 'processing' | 'success' | 'error';
const number = (value: number | string) => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(Number(value));
const size = (bytes: number) => bytes < 1024 * 1024
  ? `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(bytes / 1024)} КБ`
  : `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(bytes / 1024 / 1024)} МБ`;

function RocketScene({ phase, ready }: { phase: Phase; ready: number }) {
  return <div className={`launch-scene launch-${phase}`} aria-hidden="true">
    <div className="launch-orbit orbit-one" /><div className="launch-orbit orbit-two" />
    <span className="launch-star star-one">+</span><span className="launch-star star-two">+</span>
    <div className="launch-planet" />
    {REQUIRED_FILES.map((file, index) => <div key={file.name} className={`orbit-file orbit-file-${index} ${index < ready ? 'is-ready' : ''}`}>
      <FileText size={21} strokeWidth={1.5} /><span>CSV</span>
    </div>)}
    <div className="rocket-position"><div className="rocket-body">
      <svg viewBox="0 0 140 210" className="rocket-illustration" fill="none">
        <path className="rocket-flame" d="M55 158Q43 183 70 204Q97 183 85 158Z" fill="#ffcf24" />
        <path className="rocket-inner-flame" d="M62 158Q55 174 70 187Q85 174 78 158Z" fill="#fff5c6" />
        <path d="M46 112C24 119 18 138 21 162L49 149M94 112C116 119 122 138 119 162L91 149" fill="#ffcf24" stroke="#20221e" strokeWidth="2.5" strokeLinejoin="round" />
        <path d="M70 16C46 37 36 68 39 107L47 151H93L101 107C104 68 94 37 70 16Z" fill="white" stroke="#20221e" strokeWidth="2.5" />
        <path d="M70 16C58 26 50 40 45 53H95C90 40 82 26 70 16Z" fill="#ffcf24" stroke="#20221e" strokeWidth="2.5" />
        <circle cx="70" cy="84" r="19" fill="#f6f7f4" stroke="#20221e" strokeWidth="2.5" />
        <circle cx="70" cy="84" r="12" fill="#ffcf24" />
        <path d="M65 78L62 84" stroke="white" strokeWidth="3" strokeLinecap="round" />
        <path d="M47 139H93M52 152L54 161H86L88 152" stroke="#20221e" strokeWidth="2.5" strokeLinejoin="round" />
        <path d="M70 126V163" stroke="#20221e" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
    </div></div>
    <div className="launch-pad" />
    <div className="launch-success-mark"><Check size={32} strokeWidth={2.5} /></div>
  </div>;
}

function SummaryMetric({ icon, label, value, note }: { icon: ReactNode; label: string; value: ReactNode; note: string }) {
  return <article className="stat"><div className="stat-label">{label}{icon}</div><strong>{value}</strong><small>{note}</small></article>;
}

function DatasetSummary({ dataset }: { dataset: Dataset }) {
  const segments = Object.entries(dataset.summary.segments).filter(([, counts]) => Object.keys(counts).length > 0);
  const hasBaseline = dataset.summary.baseline_arpu !== null && dataset.summary.baseline_arpu !== undefined;
  const source = dataset.summary.source_kind === 'demo' ? 'Демоданные' : dataset.summary.source_kind === 'upload' ? 'Ваши CSV-файлы' : 'Импортированный набор';
  return <section className="dataset-summary" aria-labelledby="dataset-summary-title">
    <div className="section-heading"><h2 id="dataset-summary-title">Текущий набор данных</h2><span className="source-badge"><CheckCircle2 size={14} />{source}</span></div>
    <div className="stats-grid">
      <SummaryMetric icon={<Users size={18} />} label="Абоненты" value={<CountUp to={dataset.customer_count} />} note={dataset.summary.format === 'raw_csv' ? 'Уникальные абоненты в traffic.csv' : 'Уникальные записи в наборе'} />
      <SummaryMetric icon={<Layers size={18} />} label="Тарифы" value={<CountUp to={dataset.summary.tariff_count} />} note="В справочнике тарифов" />
      <SummaryMetric icon={<Wallet size={18} />} label="Базовая выручка" value={hasBaseline ? number(dataset.summary.baseline_arpu!) : '—'} note={hasBaseline ? 'у. е. · прогноз без кампаний' : 'Для CSV-набора пока не рассчитана'} />
    </div>
    {dataset.summary.file_rows && <div className="dataset-file-counts">{Object.entries(dataset.summary.file_rows).map(([name, count]) =>
      <div key={name}><FileText size={15} /><span>{name}</span><strong>{number(count)} строк</strong></div>)}</div>}
    {segments.length > 0 ? <div className="workflow-grid">{segments.map(([key, counts]) => <article className="panel" key={key}>
      <h3>{{ arpu_segment: 'Расходы клиентов', data_segment: 'Интернет', call_segment: 'Звонки' }[key] || key}</h3>
      {Object.entries(counts).map(([label, count]) => <div className="segment" key={label}><div><span>{label}</span><strong>{number(count)}</strong></div>
        <div className="bar"><span style={{ width: `${dataset.customer_count > 0 ? Math.min(100, count / dataset.customer_count * 100) : 0}%` }} /></div></div>)}
    </article>)}</div> : <p className="dataset-data-note"><ShieldCheck size={16} />Файлы импортированы. Сегменты и прогноз выручки для этого набора пока не рассчитаны.</p>}
    <p className="subtle dataset-imported">{dataset.name} · Импортирован {new Date(dataset.imported_at).toLocaleString('ru-RU')}</p>
  </section>;
}

export default function DataPage({ dataset, onImported }: { dataset: Dataset | null; onImported: (dataset: Dataset) => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [requestError, setRequestError] = useState<unknown>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [percent, setPercent] = useState<number | null>(null);
  const [mode, setMode] = useState<'demo' | 'upload'>('upload');
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const successHeading = useRef<HTMLHeadingElement>(null);
  const mounted = useRef(true);
  const busyRef = useRef(false);
  const dragDepth = useRef(0);
  const busy = ['preparing', 'uploading', 'processing'].includes(phase);
  const totalSize = files.reduce((sum, file) => sum + file.size, 0);
  const ready = files.length === REQUIRED_FILES.length;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => { if (phase === 'success') successHeading.current?.focus(); }, [phase]);

  function selectFiles(incoming: File[]) {
    if (busyRef.current) return;
    const next = [...files];
    const errors: string[] = [];
    let bytes = totalSize;
    for (const file of incoming) {
      if (!REQUIRED_FILES.some(required => required.name === file.name)) {
        errors.push(`«${file.name}» не входит в набор. Выберите CSV-файл с одним из четырёх имён в списке ниже.`);
      } else if (next.some(selected => selected.name === file.name)) {
        errors.push(`«${file.name}» уже добавлен. Чтобы заменить файл, сначала удалите его из списка.`);
      } else if (file.size === 0) {
        errors.push(`«${file.name}» пуст. Выберите файл с заголовком и данными.`);
      } else if (file.size > MAX_FILE_SIZE) {
        errors.push(`«${file.name}» больше 30 МБ. Уменьшите файл и добавьте его снова.`);
      } else if (bytes + file.size > MAX_TOTAL_SIZE) {
        errors.push(`«${file.name}» не добавлен: суммарный размер превысит 50 МБ. Уменьшите размер файлов.`);
      } else { next.push(file); bytes += file.size; }
    }
    setFiles(next); setValidationErrors(errors); setRequestError(null); setPhase('idle');
  }

  function removeFile(name: string) {
    if (busyRef.current) return;
    setFiles(previous => previous.filter(file => file.name !== name));
    setValidationErrors([]); setRequestError(null); setPhase('idle');
  }

  function dropFiles(event: DragEvent<HTMLDivElement>) {
    event.preventDefault(); dragDepth.current = 0; setDragging(false);
    if (!busyRef.current) selectFiles(Array.from(event.dataTransfer.files));
  }

  async function startImport(selectedMode: 'demo' | 'upload') {
    if (busyRef.current || (selectedMode === 'upload' && !ready)) return;
    busyRef.current = true;
    setMode(selectedMode); setPhase(selectedMode === 'demo' ? 'processing' : 'preparing');
    setPercent(null); setRequestError(null); setValidationErrors([]); setDragging(false);
    const controller = new AbortController();
    // A route change cannot cancel a mutation that the server may already have committed.
    // Keep its response connected to App; only the deadline can stop waiting for it.
    const timeout = window.setTimeout(() => controller.abort(), 5 * 60 * 1000);
    try {
      const imported = selectedMode === 'demo' ? await api.importDemo(controller.signal)
        : await api.importDataset(files, progress => {
          if (!mounted.current) return;
          setPhase(progress.phase);
          if (progress.phase === 'uploading') setPercent(progress.percent);
        }, controller.signal);
      onImported(imported);
      if (mounted.current) setPhase('success');
    } catch (error) {
      if (mounted.current) { setRequestError(error); setPhase('error'); }
    } finally {
      busyRef.current = false;
      window.clearTimeout(timeout);
    }
  }

  const stageTitle = phase === 'preparing' ? 'Готовим загрузку'
    : phase === 'uploading' ? 'Передаём CSV-файлы'
      : phase === 'processing' ? (mode === 'demo' ? 'Проверяем демоданные' : 'Проверяем данные на сервере')
        : phase === 'success' ? 'Данные готовы к работе' : 'Дадим данным старт';
  const stageText = phase === 'preparing' ? 'Подготавливаем файлы к передаче.'
    : phase === 'uploading' ? 'Файлы отправляются на сервер. Оставьте эту страницу открытой.'
      : phase === 'processing' ? 'Проверяем структуру CSV и связи между файлами. Это может занять немного времени.'
        : phase === 'success' ? 'Набор сохранён. Его состав уже доступен ниже и на странице обзора.'
          : 'Попробуйте сервис на демоданных или загрузите четыре CSV-файла своей аудитории.';

  return <div className="data-page">
    <div className="data-page-heading"><h1>Данные для решений</h1><p>Всё начинается с вашей аудитории.</p></div>
    <section className={`import-launch-panel ${busy ? 'is-busy' : ''}`} aria-labelledby="import-title" aria-busy={busy}>
      <div className="import-launch-copy">
        <div className="import-stage-label"><span />{phase === 'success' ? 'Импорт завершён' : busy ? 'Импорт выполняется' : 'Готовы к запуску'}</div>
        <div aria-live="polite" aria-atomic="true"><h2 id="import-title" ref={successHeading} tabIndex={phase === 'success' ? -1 : undefined}>{stageTitle}</h2><p>{stageText}</p></div>
        {busy ? <div className="import-progress-block">
          <div className="import-progress-label"><LoaderCircle size={16} className="import-spinner" /><span>{phase === 'uploading' ? 'Передача файлов' : phase === 'preparing' ? 'Подготовка запроса' : 'Проверка и сохранение'}</span>
            {phase === 'uploading' && percent !== null && <strong>{percent}%</strong>}</div>
          {phase === 'uploading' && <div className={`upload-progress ${percent === null ? 'indeterminate' : ''}`} role="progressbar" aria-label="Передача CSV-файлов" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? undefined}>
            <span style={percent !== null ? { width: `${percent}%` } : undefined} /></div>}
        </div> : <><div className="import-launch-actions"><button type="button" className={`button ${phase === 'success' ? '' : 'primary'} import-demo-button`} onClick={() => startImport('demo')}><Sparkles size={17} />Импортировать демоданные</button>
            {phase === 'success' && <NavLink to="/" className="button import-success-button">Открыть обзор<ArrowRight size={16} /></NavLink>}</div>
            {phase !== 'success' && <span className="import-demo-note">Готовый синтетический набор · без выбора файлов</span>}</>}
      </div>
      <RocketScene phase={phase} ready={mode === 'demo' && busy ? 4 : files.length} />
    </section>

    {requestError !== null && <div className="notice error import-error" role="alert"><strong>Импорт не завершён</strong>
      <p>{requestError instanceof Error ? requestError.message : 'Не удалось импортировать данные. Повторите попытку.'}</p>
      {requestError instanceof ApiError && Object.keys(requestError.fields).length > 0 && <ul>{Object.entries(requestError.fields).map(([field, value]) =>
        <li key={field}>{field}: {Array.isArray(value) ? value.map(item => typeof item === 'string' ? item : JSON.stringify(item)).join(' ') : typeof value === 'string' ? value : JSON.stringify(value)}</li>)}</ul>}
      {dataset && <span>Текущий набор данных остаётся доступен ниже.</span>}
    </div>}

    <section className="import-files-section" aria-labelledby="upload-title">
      <div className="import-files-heading"><div><h2 id="upload-title">Загрузить свои данные</h2><p>Соберите комплект из четырёх CSV. Можно добавлять по одному.</p></div><span className={`file-readiness ${ready ? 'complete' : ''}`}>{ready ? <CheckCircle2 size={15} /> : <Layers size={15} />}{files.length} из 4</span></div>
      <div className={`file-dropzone ${dragging ? 'is-dragging' : ''} ${busy ? 'is-disabled' : ''}`}
        onDragEnter={event => { event.preventDefault(); if (!busyRef.current) { dragDepth.current += 1; setDragging(true); } }}
        onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = busy ? 'none' : 'copy'; }}
        onDragLeave={event => { event.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current - 1); if (dragDepth.current === 0) setDragging(false); }}
        onDrop={dropFiles}>
        <span className="upload-icon"><Upload size={23} strokeWidth={1.5} /></span>
        <div className="dropzone-copy"><strong>{dragging ? 'Отпустите файлы здесь' : 'Перетащите CSV-файлы сюда'}</strong><span id="upload-limits">До 30 МБ на файл и 50 МБ на весь комплект</span></div>
        <button type="button" className="button choose-files-button" disabled={busy} onClick={() => input.current?.click()} aria-describedby="upload-limits">Выбрать файлы</button>
        <input ref={input} type="file" multiple accept=".csv,text/csv" className="file-input" tabIndex={-1} aria-label="CSV-файлы для импорта" disabled={busy}
          onChange={event => { selectFiles(Array.from(event.target.files ?? [])); event.target.value = ''; }} />
      </div>
      {validationErrors.length > 0 && <div className="notice error selection-errors" role="alert"><ul>{validationErrors.map((error, index) => <li key={index}>{error}</li>)}</ul></div>}
      <ul className="file-manifest" aria-label="Обязательные файлы">{REQUIRED_FILES.map(required => {
        const file = files.find(selected => selected.name === required.name);
        return <li key={required.name} className={file ? 'file-selected' : ''}>
          <span className="file-state-icon">{file ? <Check size={17} /> : <Circle size={16} />}</span>
          <span className="file-detail"><strong>{required.name}</strong><span>{required.label}</span></span>
          <span className="file-size">{file ? size(file.size) : 'Ожидает файл'}</span>
          {file ? <button type="button" className="remove-file" aria-label={`Удалить ${file.name}`} disabled={busy} onClick={() => removeFile(file.name)}><X size={16} /></button>
            : <span className="remove-file-space" />}
        </li>;
      })}</ul>
      <div className="import-submit-row"><span aria-live="polite">{ready ? `Комплект готов · ${size(totalSize)}` : files.length ? `Добавьте ещё ${4 - files.length} ${files.length === 3 ? 'файл' : 'файла'}` : 'Все четыре файла нужны для импорта'}</span>
        <button type="button" className="button primary" disabled={busy || !ready} onClick={() => startImport('upload')}><Upload size={16} />{busy && mode === 'upload' ? 'Импортируем…' : 'Импортировать файлы'}</button>
      </div>
    </section>
    {dataset && <DatasetSummary dataset={dataset} />}
  </div>;
}
