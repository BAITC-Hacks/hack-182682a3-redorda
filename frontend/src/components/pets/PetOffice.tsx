import type { ReactNode } from 'react';
import { ArrowUpRight, Pause, Play } from 'lucide-react';

function Desk({ index, label }: { index: number; label: string }) {
  return <div className={`pet-desk pet-desk-${index}`}><span>{label}</span>
    <svg viewBox="0 0 120 84" shapeRendering="crispEdges" fill="none">
      <path d="M4 48h112v24H4zM10 72h8v12h-8zM102 72h8v12h-8z" fill="#686d61" />
      <path d="M4 44h112v16H4z" fill="#939383" /><path d="M4 60h112v5H4z" fill="#757b6c" />
      <path d="M36 10h48v32H36zM56 42h8v6h-8zM48 48h24v4H48z" fill="#152731" />
      <path d="M40 14h40v24H40z" fill="#1d323d" /><path d="M44 20h20v2H44zM44 26h28v2H44zM44 32h16v2H44z" fill={['#b8a2e5', '#7dbbd0', '#e4a6c0', '#e7cb78'][index]} opacity=".65" />
      <path d="M42 56h36v4H42z" fill="#344b51" />
      <path d="M94 33h8v12h-8zM102 35h4v6h-4z" fill={['#b8a2e5', '#7dbbd0', '#e4a6c0', '#e7cb78'][index]} />
      <path d="M14 49h12v6H14z" fill="#ced9c6" />
    </svg>
  </div>;
}

export default function PetOffice({ children, paused, reducedMotion, title, onPause, onRelease }: {
  children: ReactNode; paused: boolean; reducedMotion: boolean; title: string;
  onPause: () => void; onRelease: () => void;
}) {
  return <section className={`pet-office${paused || reducedMotion ? ' is-paused' : ''}`} aria-label="Мини-офис Janymda">
    <div className="pet-office-header"><div><span className="pet-office-logo">J<span> / </span>team</span><span className="pet-office-subtitle">Маленькая команда. Большие планы.</span></div>
      <div className="pet-office-actions"><button type="button" disabled={reducedMotion} onClick={onPause} aria-label={paused ? 'Продолжить сценку в офисе' : 'Приостановить сценку в офисе'}>{paused || reducedMotion ? <Play size={14} /> : <Pause size={14} />}</button>
        <button type="button" onClick={onRelease}>Выпустить на экран<ArrowUpRight size={14} /></button></div>
    </div>
    <div className="pet-office-floor">
      <div className="pet-office-wall" aria-hidden="true"><span>janymda</span><i /><i /><i /><i /></div>
      <div className="pet-office-furniture" aria-hidden="true">
        <Desk index={0} label="Идеи" /><Desk index={1} label="Данные" /><Desk index={2} label="Связь" /><Desk index={3} label="Планы" />
        <div className="pet-office-rug"><span>J / TEAM</span><small>жаныңда · рядом</small></div>
        <div className="pet-plant pet-plant-left"><i /><b /></div><div className="pet-plant pet-plant-right"><i /><b /></div>
      </div>
      {children}
    </div>
    <div className="pet-office-footer"><span><i />{paused || reducedMotion ? 'Команда отдыхает' : title}</span><small>Нажми на персонажа — поздоровайся</small></div>
  </section>;
}
