import '@testing-library/jest-dom/vitest';

class NoopIntersectionObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.IntersectionObserver = NoopIntersectionObserver as unknown as typeof IntersectionObserver;
