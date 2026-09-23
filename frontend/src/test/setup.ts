import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); sessionStorage.clear(); });

class NoopIntersectionObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.IntersectionObserver = NoopIntersectionObserver as unknown as typeof IntersectionObserver;

// jsdom has no layout or media-query implementation; keep real pet components mounted.
globalThis.ResizeObserver = NoopIntersectionObserver as unknown as typeof ResizeObserver;
Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true,
  value: (query: string) => ({ matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
    dispatchEvent: () => false }),
});
