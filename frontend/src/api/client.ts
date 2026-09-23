import type { Dataset, Meta, Page, Run, RunInput, Session } from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string, public fields: Record<string, unknown> = {},
    public code = '') {
    super(message);
  }
}

let csrfToken: string | null = null;
const unauthorizedListeners = new Set<() => void>();

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || 'GET').toUpperCase();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json', ...(init?.headers as Record<string, string> | undefined),
  };
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    headers['X-CSRFToken'] = csrfToken || await csrf();
  }
  const response = await fetch(`/api/v1/${path}`, {
    credentials: 'same-origin', ...init, headers,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const code = payload?.error?.code || '';
    if (code === 'not_authenticated') unauthorizedListeners.forEach(listener => listener());
    throw new ApiError(response.status, payload?.error?.message || 'Не удалось выполнить запрос.',
      payload?.error?.fields || {}, code);
  }
  if (payload === null) throw new ApiError(502, 'Сервер вернул некорректный ответ.');
  if (typeof payload?.csrf_token === 'string') csrfToken = payload.csrf_token;
  return payload as T;
}

async function csrf(): Promise<string> {
  const result = await request<{ csrf_token: string }>('auth/csrf/');
  csrfToken = result.csrf_token;
  return csrfToken;
}

export const api = {
  me: () => request<Session>('auth/me/'),
  login: async (username: string, password: string) => {
    await csrf();
    return request<Session>('auth/login/', {
      method: 'POST', body: JSON.stringify({ username, password }),
    });
  },
  logout: () => request<Session>('auth/logout/', { method: 'POST' }),
  onUnauthorized: (listener: () => void) => {
    unauthorizedListeners.add(listener);
    return () => { unauthorizedListeners.delete(listener); };
  },
  meta: () => request<Meta>('meta/'),
  dataset: () => request<Dataset>('datasets/current/'),
  runs: (page = 1) => request<Page<Run>>(`runs/?page=${page}`),
  run: (id: string) => request<Run>(`runs/${id}/`),
  createRun: (input: RunInput) => request<Run>('runs/', {
    method: 'POST', body: JSON.stringify(input),
  }),
};
