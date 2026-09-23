import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { ChevronDown, Coffee, FileText, Heart, Pause, Phone, Play, Sparkles, X } from 'lucide-react';
import PetAvatar from './PetAvatar';
import PetOffice from './PetOffice';
import { PETS, sampleScene, SCENE_MS, TRAVEL_MS } from './choreography';
import './AgentPets.css';

const STORAGE_KEY = 'janymda-pets';
function readPreference(key: string) {
  try { return localStorage.getItem(`${STORAGE_KEY}-${key}`) === 'true'; } catch { return false; }
}

export default function AgentPets({ home }: { home: HTMLDivElement | null }) {
  const [hidden, setHidden] = useState(() => readPreference('hidden'));
  const [paused, setPaused] = useState(() => readPreference('paused'));
  const [roaming, setRoaming] = useState(() => readPreference('roaming'));
  const [sceneRequest, setSceneRequest] = useState(0);
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [pageVisible, setPageVisible] = useState(!document.hidden);
  const [panelOpen, setPanelOpen] = useState(false);
  const [greeting, setGreeting] = useState<number | null>(null);
  const [moment, setMoment] = useState(() => sampleScene(TRAVEL_MS));
  const elapsed = useRef(TRAVEL_MS);
  const stage = useRef<HTMLDivElement>(null);
  const actors = useRef<(HTMLButtonElement | null)[]>([]);
  const parcel = useRef<HTMLDivElement>(null);
  const dock = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const frozen = paused || reducedMotion || !pageVisible;

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const motionChanged = () => setReducedMotion(media.matches);
    const visibilityChanged = () => setPageVisible(!document.hidden);
    media.addEventListener('change', motionChanged);
    document.addEventListener('visibilitychange', visibilityChanged);
    return () => {
      media.removeEventListener('change', motionChanged);
      document.removeEventListener('visibilitychange', visibilityChanged);
    };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(`${STORAGE_KEY}-hidden`, String(hidden));
      localStorage.setItem(`${STORAGE_KEY}-paused`, String(paused));
      localStorage.setItem(`${STORAGE_KEY}-roaming`, String(roaming));
    } catch { /* Pets also work when browser storage is unavailable. */ }
  }, [hidden, paused, roaming]);

  useEffect(() => {
    if (greeting === null) return;
    const timer = window.setTimeout(() => setGreeting(null), 3200);
    return () => window.clearTimeout(timer);
  }, [greeting]);

  useEffect(() => {
    if (!panelOpen) return;
    const outside = (event: PointerEvent) => {
      if (!dock.current?.contains(event.target as Node)) setPanelOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setPanelOpen(false); toggle.current?.focus(); }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [panelOpen]);

  useEffect(() => {
    if (hidden || !stage.current) return;
    let width = stage.current.clientWidth;
    let height = stage.current.clientHeight;
    let previousTime: number | null = null;
    let frameId = 0;
    let lastMoment = '';
    const paint = () => {
      const size = actors.current[0]?.offsetWidth ?? 76;
      const frame = sampleScene(elapsed.current, Math.min(.32, (size + 16) / Math.max(width, 1)));
      frame.positions.forEach((point, index) => {
        const actor = actors.current[index];
        if (!actor) return;
        actor.style.transform = `translate3d(${point.x * width}px, ${point.y * height}px, 0)`;
        actor.style.setProperty('--pet-direction', String(point.direction));
      });
      if (parcel.current) {
        const from = frame.positions[frame.scene.pair[0]];
        const to = frame.positions[frame.scene.pair[1]];
        const t = frame.transfer;
        const x = (from.x + (to.x - from.x) * t) * width + size * .54;
        const y = (from.y + (to.y - from.y) * t) * height + size * .58 - Math.sin(t * Math.PI) * 42;
        parcel.current.style.transform = `translate3d(${x}px, ${y}px, 0) rotate(${Math.sin(t * Math.PI * 2) * 14}deg)`;
        parcel.current.style.opacity = frame.scene.kind === 'document' ? '1' : '0';
      }
      const key = `${frame.index}-${frame.moving}`;
      if (key !== lastMoment) { lastMoment = key; setMoment(frame); }
    };
    const observer = new ResizeObserver(() => {
      if (!stage.current) return;
      width = stage.current.clientWidth;
      height = stage.current.clientHeight;
      paint();
    });
    observer.observe(stage.current);
    const tick = (time: number) => {
      if (previousTime !== null) elapsed.current += Math.min(time - previousTime, 80);
      previousTime = time;
      paint();
      frameId = requestAnimationFrame(tick);
    };
    paint();
    if (!frozen) frameId = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(frameId); observer.disconnect(); };
  }, [hidden, frozen, roaming, home, panelOpen, sceneRequest]);

  function chooseScene(index: number) {
    elapsed.current = index * SCENE_MS;
    setHidden(false);
    setPaused(false);
    // Changing the scene also works when the system has reduced motion enabled.
    if (reducedMotion) elapsed.current += TRAVEL_MS;
    setMoment(sampleScene(elapsed.current));
    setSceneRequest(value => value + 1);
    if (roaming || home) setPanelOpen(false);
    toggle.current?.focus();
  }

  const { scene, moving } = moment;
  const cast = <div className="pet-stage" ref={stage} role="group" aria-label="Мини-агенты Janymda">
      {PETS.map((pet, index) => {
        const partner = scene.pair.indexOf(index);
        const activity = !moving && partner !== -1 ? scene.kind : 'idle';
        return <button key={pet.name} ref={element => { actors.current[index] = element; }}
          type="button" className={`pet-actor${moving ? ' is-walking' : ''}${greeting === index ? ' is-waving' : ''}`}
          data-activity={activity} style={{ '--pet-color': pet.color, '--pet-delay': `${index * -.17}s` } as CSSProperties}
          aria-label={`${pet.name} — ${pet.role}. Поздороваться`}
          onFocus={event => { if (event.target.matches(':focus-visible')) setPaused(true); }}
          onClick={() => setGreeting(current => current === index ? null : index)}>
          <span className={`pet-bubble${greeting === index || (!moving && partner !== -1) ? ' is-visible' : ''}`} aria-hidden="true">
            {greeting === index ? pet.greeting : partner !== -1 ? scene.lines[partner] : pet.greeting}
          </span>
          <PetAvatar petIndex={index} />
          <span className="pet-name">{pet.name}</span>
          {activity === 'call' && <span className="pet-prop pet-phone" aria-hidden="true"><Phone size={19} fill="currentColor" /><i /><i /></span>}
          {activity === 'coffee' && <span className="pet-prop pet-coffee" aria-hidden="true"><Coffee size={23} /><i /></span>}
          {activity === 'celebrate' && <span className="pet-prop pet-heart" aria-hidden="true"><Heart size={20} fill="currentColor" /></span>}
        </button>;
      })}
      <div className="pet-parcel" ref={parcel} aria-hidden="true"><FileText size={19} strokeWidth={1.7} /><span /></div>
    </div>;
  const office = <PetOffice paused={frozen} reducedMotion={reducedMotion} title={scene.title}
    onPause={() => setPaused(value => !value)} onRelease={() => { setRoaming(true); setPanelOpen(false); }}>{cast}</PetOffice>;

  return <>
    {!hidden && !roaming && home && createPortal(office, home)}
    <div className={`agent-pets${frozen ? ' is-paused' : ''}`}>
    {!hidden && roaming && cast}

    <div className="pet-dock" ref={dock}>
      {panelOpen && <section className={`pet-panel${!roaming && !home && !hidden ? ' has-office' : ''}`} id="pet-panel" aria-label="Настройки мини-агентов">
        <div className="pet-panel-heading"><div><strong>Твоя маленькая команда</strong><p>Четверо. Всегда рядом.</p></div>
          <button type="button" className="pet-icon-button" aria-label="Закрыть настройки мини-агентов" onClick={() => { setPanelOpen(false); toggle.current?.focus(); }}><X size={16} /></button>
        </div>
        {!hidden && !roaming && !home ? office : <div className="pet-roster">{PETS.map((pet, index) => <div key={pet.name}><PetAvatar petIndex={index} /><strong>{pet.name}</strong><span>{pet.role}</span></div>)}</div>}
        <p className="pet-panel-caption">Устроим маленькую сценку?</p>
        <div className="pet-scene-buttons">
          <button type="button" onClick={() => chooseScene(0)}><FileText size={15} />Передать идею</button>
          <button type="button" onClick={() => chooseScene(1)}><Phone size={15} />Созвониться</button>
          <button type="button" onClick={() => chooseScene(3)}><Coffee size={15} />На чай</button>
        </div>
        <p className="pet-disclaimer">Просто оживляют экран. Настоящие дела — за тобой.</p>
        {reducedMotion && <p className="pet-motion-note">Движение отключено настройкой твоего устройства.</p>}
        <button type="button" className="pet-mode-button" onClick={() => { setRoaming(value => !value); setHidden(false); if (!roaming || home) setPanelOpen(false); }}>
          {roaming ? 'Собрать всех в мини-офисе' : 'Выпустить на экран'}
        </button>
        <button type="button" className="pet-hide-button" onClick={() => { setHidden(value => !value); setPanelOpen(false); toggle.current?.focus(); }}>
          {hidden ? 'Вернуть команду на экран' : 'Спрятать команду'}
        </button>
      </section>}
      <div className="pet-dock-bar">
        <button type="button" className="pet-dock-toggle" ref={toggle} aria-expanded={panelOpen} aria-controls="pet-panel" onClick={() => setPanelOpen(value => !value)}>
          <span className="pet-dock-mark" aria-hidden="true"><Sparkles size={17} /></span>
          <span><strong>Команда рядом</strong><small>{hidden ? 'Позвать мини-агентов' : frozen ? 'Тихий режим' : '4 мини-агента'}</small></span>
          <ChevronDown size={14} className={panelOpen ? 'pet-chevron-open' : ''} />
        </button>
        {!hidden && <button type="button" className="pet-icon-button pet-pause" disabled={reducedMotion}
          aria-label={paused ? 'Продолжить движение агентов' : 'Приостановить движение агентов'}
          title={reducedMotion ? 'Движение отключено в настройках устройства' : paused ? 'Продолжить' : 'Пауза'}
          onClick={() => setPaused(value => !value)}>{paused || reducedMotion ? <Play size={15} /> : <Pause size={15} />}</button>}
      </div>
    </div>
  </div></>;
}
