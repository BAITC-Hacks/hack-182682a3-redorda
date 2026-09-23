import type { Dataset, Meta, Page, Run, RunEventsPage, RunInput, RunResult, Session } from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string, public fields: Record<string, unknown> = {},
    public code = '') {
    super(message);
  }
}

let csrfToken: string | null = null;
let csrfRequest: Promise<string> | null = null;
const authListeners = new Set<() => void>();

export function onAuthenticationRequired(listener: () => void) {
  authListeners.add(listener);
  return () => { authListeners.delete(listener); };
}

async function responseError(response: Response, path: string): Promise<never> {
  const payload = await response.json().catch(() => null);
  if (response.status === 401 || response.status === 403) {
    csrfToken = null;
    if (!path.startsWith('auth/') || path === 'auth/logout/') {
      authListeners.forEach(listener => listener());
    }
  }
  throw new ApiError(response.status, payload?.error?.message || 'Не удалось выполнить запрос.',
    payload?.error?.fields || {}, payload?.error?.code || '');
}

async function getCsrfToken(): Promise<string> {
  if (csrfToken) return csrfToken;
  if (!csrfRequest) {
    csrfRequest = request<{ csrf_token: string }>('auth/csrf/').then(payload => {
      if (!payload.csrf_token) throw new ApiError(502, 'Сервер не вернул CSRF-токен.');
      csrfToken = payload.csrf_token;
      return csrfToken;
    }).finally(() => { csrfRequest = null; });
  }
  return csrfRequest;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('Accept', 'application/json');
  if (init?.body) headers.set('Content-Type', 'application/json');
  if (init?.method && !['GET', 'HEAD', 'OPTIONS'].includes(init.method.toUpperCase())) {
    headers.set('X-CSRFToken', await getCsrfToken());
  }
  const response = await fetch(`/api/v1/${path}`, { ...init, credentials: 'include', headers });
  if (!response.ok) return responseError(response, path);
  const payload = await response.json().catch(() => null);
  if (payload === null) throw new ApiError(502, 'Сервер вернул некорректный ответ.');
  return payload as T;
}

async function updateSession(path: string, body?: Record<string, string>) {
  const session = await request<Session>(path, {
    method: 'POST', ...(body ? { body: JSON.stringify(body) } : {}),
  });
  csrfToken = session.csrf_token || null;
  return session;
}

export const api = {
  session: (signal?: AbortSignal) => request<Session>('auth/me/', { signal }),
  login: (username: string, password: string) => updateSession('auth/login/', { username, password }),
  logout: () => updateSession('auth/logout/'),
  meta: (signal?: AbortSignal) => request<Meta>('meta/', { signal }),
  dataset: (signal?: AbortSignal) => request<Dataset>('datasets/current/', { signal }),
  runs: (page = 1, signal?: AbortSignal) => request<Page<Run>>(`runs/?page=${page}`, { signal }),
  run: (id: string, signal?: AbortSignal) => request<Run>(`runs/${id}/`, { signal }),
  createRun: (input: RunInput) => request<Run>('runs/', {
    method: 'POST', body: JSON.stringify(input),
  }),
  startRun: (id: string, idempotencyKey: string) => request<Run>(`runs/${id}/start/`, {
    method: 'POST', headers: { 'Idempotency-Key': idempotencyKey },
  }),
  cancelRun: (id: string) => request<Run>(`runs/${id}/cancel/`, { method: 'POST' }),
  events: (id: string, after: number, signal?: AbortSignal) => request<RunEventsPage>(
    `runs/${id}/events/?after=${after}&limit=100`, { signal }),
  results: (id: string, signal?: AbortSignal) => request<RunResult>(`runs/${id}/results/`, { signal }),
  async exportRun(id: string): Promise<Blob> {
    const path = `runs/${id}/export/`;
    const response = await fetch(`/api/v1/${path}`, {
      credentials: 'include', headers: { Accept: 'text/csv, application/json' },
    });
    if (!response.ok) return responseError(response, path);
    return response.blob();
  },
};
