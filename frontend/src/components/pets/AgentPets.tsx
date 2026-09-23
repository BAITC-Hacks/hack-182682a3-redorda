import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { ChevronDown, FileText, Pause, Play, Sparkles, X } from 'lucide-react';
import type { RunEvent, TeamTask } from '../../api/types';
import { useRun } from '../../state/RunContext';
import PetAvatar from './PetAvatar';
import PetOffice from './PetOffice';
import { DESKS, PETS, handoffActors, latestTaskForPet, roleForPet } from './choreography';
import './AgentPets.css';

const STORAGE_KEY = 'janymda-pets';
const taskStatus: Record<TeamTask['status'], string> = {
  pending: 'Ожидает', running: 'Работает', completed: 'Завершено', failed: 'Ошибка', cancelled: 'Отменено',
};
function readPreference(key: string) {
  try { return localStorage.getItem(`${STORAGE_KEY}-${key}`) === 'true'; } catch { return false; }
}
function stringValue(value: unknown): string | null { return typeof value === 'string' && value.trim() ? value : null; }
function eventForTask(events: RunEvent[], taskId: string | undefined): RunEvent | null {
  return taskId ? events.filter(event => event.payload.task_id === taskId).at(-1) ?? null : null;
}
function Detail({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span>Нет данных</span>;
  return <pre className="pet-data">{JSON.stringify(value, null, 2)}</pre>;
}

export default function AgentPets({ home }: { home: HTMLDivElement | null }) {
  const { selectedId, run, events, team, liveHandoff, teamUnavailable } = useRun();
  const [hidden, setHidden] = useState(() => readPreference('hidden'));
  const [paused, setPaused] = useState(() => readPreference('paused'));
  const [roaming, setRoaming] = useState(() => readPreference('roaming'));
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [pageVisible, setPageVisible] = useState(!document.hidden);
  const [panelOpen, setPanelOpen] = useState(false);
  const [selectedPet, setSelectedPet] = useState<number | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [handoffPhase, setHandoffPhase] = useState<'idle' | 'start' | 'moving'>('idle');
  const stage = useRef<HTMLDivElement>(null);
  const actors = useRef<(HTMLButtonElement | null)[]>([]);
  const parcel = useRef<HTMLButtonElement>(null);
  const dock = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const panelClose = useRef<HTMLButtonElement>(null);
  const lastAnimatedHandoff = useRef<number | null>(null);
  const frozen = paused || reducedMotion || !pageVisible;
  const handoff = handoffActors(liveHandoff);
  const tasks = team?.tasks ?? [];
  const petTasks = useMemo(() => PETS.map((_, index) => latestTaskForPet(tasks, events, index)), [tasks, events]);
  const selectedTask = tasks.find(task => task.id === selectedTaskId) ?? (selectedPet === null ? null : petTasks[selectedPet]);
  const selectedEvent = eventForTask(events, selectedTask?.id);
  const selectedArtifacts = team?.artifacts.filter(artifact => selectedTask?.artifact_ids.includes(artifact.id)) ?? [];
  const taskEvidence = selectedTask?.evidence_ids ?? [];
  const failure = selectedTask?.status === 'failed' ? stringValue(selectedEvent?.payload.reason) ?? stringValue(selectedEvent?.payload.message) : null;
  const title = !selectedId ? 'Ожидание плана — команда пока отдыхает'
    : teamUnavailable ? 'Данные команды пока недоступны'
    : handoffPhase !== 'idle' && handoff ? `${PETS[handoff.from].name} передаёт задачу ${PETS[handoff.to].name}`
    : tasks.some(task => task.status === 'running') ? 'Команда работает по событиям запуска'
    : run?.status === 'completed' ? 'Запуск завершён'
    : run?.status === 'failed' ? 'Запуск завершился с ошибкой'
    : run?.status === 'cancelled' ? 'Запуск отменён' : 'Команда ожидает задачу';

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const motionChanged = () => setReducedMotion(media.matches);
    const visibilityChanged = () => setPageVisible(!document.hidden);
    media.addEventListener('change', motionChanged);
    document.addEventListener('visibilitychange', visibilityChanged);
    return () => { media.removeEventListener('change', motionChanged); document.removeEventListener('visibilitychange', visibilityChanged); };
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(`${STORAGE_KEY}-hidden`, String(hidden));
      localStorage.setItem(`${STORAGE_KEY}-paused`, String(paused));
      localStorage.setItem(`${STORAGE_KEY}-roaming`, String(roaming));
    } catch { /* Storage is optional. */ }
  }, [hidden, paused, roaming]);
  useEffect(() => {
    setSelectedPet(null); setSelectedTaskId(null); setHandoffPhase('idle'); lastAnimatedHandoff.current = null;
  }, [selectedId]);
  useEffect(() => {
    if (!liveHandoff || lastAnimatedHandoff.current === liveHandoff.id) return;
    lastAnimatedHandoff.current = liveHandoff.id;
    if (!handoffActors(liveHandoff) || frozen || hidden) return;
    setHandoffPhase('start');
    const frame = requestAnimationFrame(() => setHandoffPhase('moving'));
    const timer = window.setTimeout(() => setHandoffPhase('idle'), 2200);
    return () => { cancelAnimationFrame(frame); window.clearTimeout(timer); setHandoffPhase('idle'); };
  }, [liveHandoff?.id, frozen, hidden]);
  useEffect(() => {
    if (!panelOpen) return;
    panelClose.current?.focus();
    const outside = (event: PointerEvent) => {
      if (!dock.current?.contains(event.target as Node) && !stage.current?.contains(event.target as Node)) setPanelOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setPanelOpen(false); toggle.current?.focus(); }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [panelOpen]);
  function openPet(index: number, taskId?: string | null) {
    setSelectedPet(index); setSelectedTaskId(taskId ?? null); setPanelOpen(true);
  }
  const cast = <div className="pet-stage" ref={stage} role="group" aria-label="Команда Janymda">
    {PETS.map((pet, index) => {
      const task = petTasks[index];
      const actorHandoff = handoff && handoff.from === index && handoffPhase !== 'idle';
      const point = actorHandoff && handoffPhase === 'moving' ? DESKS[handoff.to] : DESKS[index];
      const role = roleForPet(index, task);
      const status = !selectedId ? 'Ожидает план' : task ? taskStatus[task.status] : 'Ожидает задачу';
      return <button key={pet.name} ref={element => { actors.current[index] = element; }} type="button"
        className={`pet-actor${actorHandoff ? ' is-walking' : ''}${task?.status === 'running' ? ' is-working' : ''}${task?.status === 'failed' ? ' has-error' : ''}`}
        data-activity={task?.status ?? 'idle'}
        style={{ '--pet-color': pet.color, '--pet-delay': `${index * -.17}s`, left: `${point.x * 100}%`, top: `${point.y * 100}%` } as CSSProperties}
        aria-label={`${pet.name} — ${role}. ${status}. Открыть рабочую панель`}
        onClick={() => openPet(index)}>
        <span className={`pet-bubble${task?.status === 'running' || task?.status === 'failed' ? ' is-visible' : ''}`} aria-hidden="true">
          {task?.status === 'failed' ? failure ?? 'Ошибка задачи' : task?.status === 'running' ? task.title : status}
        </span>
        <PetAvatar petIndex={index} />
        <span className="pet-name">{pet.name} · {role}</span>
      </button>;
    })}
    {handoff && handoffPhase !== 'idle' && <button className={`pet-parcel pet-parcel-live${handoffPhase === 'moving' ? ' is-moving' : ''}`}
      type="button" ref={parcel} style={{ '--from-x': `${DESKS[handoff.from].x * 100}%`, '--from-y': `${DESKS[handoff.from].y * 100}%`, '--to-x': `${DESKS[handoff.to].x * 100}%`, '--to-y': `${DESKS[handoff.to].y * 100}%` } as CSSProperties}
      aria-label="Открыть переданную задачу" onClick={() => openPet(handoff.to, handoff.taskId)}><FileText size={19} strokeWidth={1.7} /><span /></button>}
  </div>;
  const office = <PetOffice paused={frozen} reducedMotion={reducedMotion} title={title} planName={run?.name ?? null}
    onPause={() => setPaused(value => !value)} onRelease={() => { setRoaming(true); setPanelOpen(false); }}>{cast}</PetOffice>;

  return <>
    {!hidden && !roaming && home && createPortal(office, home)}
    <div className={`agent-pets${frozen ? ' is-paused' : ''}`}>
      {!hidden && roaming && cast}
      <div className="pet-dock" ref={dock}>
        {panelOpen && <section className={`pet-panel${!roaming && !home && !hidden ? ' has-office' : ''}`} id="pet-panel" aria-label="Рабочая панель команды">
          <div className="pet-panel-heading"><div><strong>{selectedPet === null ? 'Живая команда' : `${PETS[selectedPet].name} · ${roleForPet(selectedPet, selectedTask)}`}</strong>
            <p>{selectedId ? run?.name ?? 'Загрузка выбранного плана…' : 'Выберите или создайте план на рабочем столе'}</p></div>
            <button type="button" ref={panelClose} className="pet-icon-button" aria-label="Закрыть рабочую панель" onClick={() => { setPanelOpen(false); toggle.current?.focus(); }}><X size={16} /></button>
          </div>
          {!hidden && !roaming && !home ? office : <div className="pet-roster">{PETS.map((pet, index) => <button type="button" key={pet.name} onClick={() => openPet(index)}><PetAvatar petIndex={index} /><strong>{pet.name}</strong><span>{roleForPet(index, petTasks[index])}</span></button>)}</div>}
          {selectedId && teamUnavailable && <p className="pet-panel-caption">Задачи команды пока недоступны в API.</p>}
          {selectedId && !teamUnavailable && <div className="pet-work-detail">
            <h3>{selectedTask?.title ?? 'Задача пока не назначена'}</h3>
            <p>Статус: {selectedTask ? taskStatus[selectedTask.status] : 'Ожидание'}{failure ? ` · ${failure}` : ''}</p>
            {selectedTask && <><p className="pet-detail-label">Входные данные и события</p>
              <Detail value={selectedEvent?.payload ?? null} />
              <p className="pet-detail-label">Результат</p>
              {selectedArtifacts.length ? selectedArtifacts.map(artifact => <div key={artifact.id} className="pet-artifact"><strong>{artifact.title}</strong><Detail value={artifact.data} />
                <small>Доказательства: {artifact.evidence_ids.length ? artifact.evidence_ids.join(', ') : 'не указаны'}</small></div>) : <p>Результат пока не сохранён.</p>}
              <p className="pet-detail-label">Доказательства задачи</p><p>{taskEvidence.length ? taskEvidence.join(', ') : 'Не указаны'}</p></>}
            {tasks.length > 0 && <div className="pet-task-list"><p className="pet-detail-label">Задачи запуска</p>{tasks.map(task => <button type="button" key={task.id} onClick={() => openPet(PETS.findIndex(pet => pet.actorIds.some(role => role === task.actor_id)), task.id)}>
              {task.title} · {taskStatus[task.status]}</button>)}</div>}
          </div>}
          {!selectedId && <p className="pet-disclaimer">Команда ждёт план. Движение персонажей не означает выполнение задачи.</p>}
          {reducedMotion && <p className="pet-motion-note">Движение отключено настройкой устройства.</p>}
          <button type="button" className="pet-mode-button" onClick={() => { setRoaming(value => !value); setHidden(false); if (!roaming || home) setPanelOpen(false); }}>
            {roaming ? 'Собрать всех в мини-офисе' : 'Выпустить на экран'}
          </button>
          <button type="button" className="pet-hide-button" onClick={() => { setHidden(value => !value); if (!hidden || roaming || home) { setPanelOpen(false); toggle.current?.focus(); } }}>
            {hidden ? 'Вернуть команду на экран' : 'Спрятать команду'}
          </button>
        </section>}
        <div className="pet-dock-bar">
          <button type="button" className="pet-dock-toggle" ref={toggle} aria-expanded={panelOpen} aria-controls="pet-panel" onClick={() => { setSelectedPet(null); setSelectedTaskId(null); setPanelOpen(value => !value); }}>
            <span className="pet-dock-mark" aria-hidden="true"><Sparkles size={17} /></span>
            <span><strong>Команда рядом</strong><small>{hidden ? 'Показать команду' : selectedId ? title : 'Ожидает план'}</small></span>
            <ChevronDown size={14} className={panelOpen ? 'pet-chevron-open' : ''} />
          </button>
          {!hidden && <button type="button" className="pet-icon-button pet-pause" disabled={reducedMotion}
            aria-label={paused ? 'Продолжить движение агентов' : 'Приостановить движение агентов'}
            title={reducedMotion ? 'Движение отключено в настройках устройства' : paused ? 'Продолжить' : 'Пауза'}
            onClick={() => setPaused(value => !value)}>{paused || reducedMotion ? <Play size={15} /> : <Pause size={15} />}</button>}
        </div>
      </div>
    </div>
  </>;
}
