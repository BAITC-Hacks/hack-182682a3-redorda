// Adapted from React Bits Spotlight Card (TypeScript/CSS variant).
// https://github.com/DavidHDev/react-bits/tree/main/src/ts-default/Components/SpotlightCard
import { useRef, type MouseEvent, type PropsWithChildren } from 'react';
import './SpotlightCard.css';

interface SpotlightCardProps extends PropsWithChildren {
  className?: string;
  spotlightColor?: string;
}

export default function SpotlightCard({
  children,
  className = '',
  spotlightColor = 'rgba(255, 207, 36, 0.2)',
}: SpotlightCardProps) {
  const ref = useRef<HTMLElement>(null);

  function handleMouseMove(event: MouseEvent<HTMLElement>) {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    ref.current.style.setProperty('--mouse-x', `${event.clientX - rect.left}px`);
    ref.current.style.setProperty('--mouse-y', `${event.clientY - rect.top}px`);
    ref.current.style.setProperty('--spotlight-color', spotlightColor);
  }

  return <article ref={ref} className={`spotlight-card ${className}`} onMouseMove={handleMouseMove}>
    {children}
  </article>;
}
