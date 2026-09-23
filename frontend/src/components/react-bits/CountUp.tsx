// Adapted from React Bits Count Up (TypeScript/CSS variant).
// https://github.com/DavidHDev/react-bits/tree/main/src/ts-default/TextAnimations/CountUp
import { useInView, useMotionValue, useReducedMotion, useSpring } from 'motion/react';
import { useEffect, useRef } from 'react';

interface CountUpProps {
  to: number;
  duration?: number;
}

const format = (value: number) => new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 0,
}).format(value);

export default function CountUp({ to, duration = 1.2 }: CountUpProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const reduceMotion = useReducedMotion();
  const isInView = useInView(ref, { once: true });
  const value = useMotionValue(0);
  const spring = useSpring(value, {
    damping: 20 + 40 / duration,
    stiffness: 100 / duration,
  });

  useEffect(() => {
    if (reduceMotion) {
      spring.jump(to);
      if (ref.current) ref.current.textContent = format(to);
    } else if (isInView) {
      value.set(to);
    }
  }, [isInView, reduceMotion, spring, to, value]);

  useEffect(() => spring.on('change', latest => {
    if (ref.current) ref.current.textContent = format(latest);
  }), [spring]);

  return <span ref={ref} aria-label={format(to)}>{format(reduceMotion ? to : 0)}</span>;
}
