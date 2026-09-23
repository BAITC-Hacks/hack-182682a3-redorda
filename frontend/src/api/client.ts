import type { ApiRun, Dataset, Meta, Page, RunEventsPage, RunInput, RunResult, Session } from './types';
import { toEvent, toResults, toRun } from './adapters';

export class ApiError extends Error {
  constructor(public status: number, message: string, public fields: Record<string, unknown> = {},
    public code = '') {
    super(message);
  }
}

export type ImportProgress = { phase: 'uploading'; percent: number | null } | { phase: 'processing' };

function responseError(status: number, payload: any) {
  const code = payload?.error?.code || '';
  if (code === 'not_authenticated') unauthorizedListeners.forEach(listener => listener());
  if (code === 'csrf_failed' || code === 'permission_denied') csrfToken = null;
  return new ApiError(status, payload?.error?.message || 'Не удалось выполнить запрос. Повторите попытку.',
    payload?.error?.fields || {}, code);
}

let csrfToken: string | null = null;
const unauthorizedListeners = new Set<() => void>();

async function request<T>(path: string, init?: RequestInit, csv = false, timeoutMs = 15000): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  init?.signal?.addEventListener('abort', abort, { once: true });
  if (init?.signal?.aborted) controller.abort();
  const timeout = window.setTimeout(abort, timeoutMs);
  const headers = new Headers(init?.headers);
  // DRF negotiates JSON errors before the streaming CSV view executes.
  headers.set('Accept', csv ? 'text/csv, application/json' : 'application/json');
  try {
    if (init?.method === 'POST') {
      headers.set('Content-Type', 'application/json');
      headers.set('X-CSRFToken', csrfToken || await csrf());
    }
    const response = await fetch(`/api/v1/${path}`, { ...init, headers, signal: controller.signal, credentials: 'same-origin' });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      const code = payload?.error?.code || '';
      if (code === 'not_authenticated') unauthorizedListeners.forEach(listener => listener());
      if (code === 'csrf_failed' || code === 'permission_denied') csrfToken = null;
      throw new ApiError(response.status, payload?.error?.message || 'Не удалось выполнить запрос. Попробуйте ещё раз.', payload?.error?.fields || {}, code);
    }
    if (csv) {
      if (!response.headers.get('Content-Type')?.toLowerCase().includes('text/csv')) {
        throw new ApiError(502, 'Вместо CSV сервер вернул другой формат. Повторите скачивание позже.');
      }
      return await response.blob() as T;
    }
    const payload = await response.json().catch(() => null);
    if (payload === null) throw new ApiError(502, 'Сервер вернул некорректный ответ.');
    if (typeof payload?.csrf_token === 'string') csrfToken = payload.csrf_token;
    return payload as T;
  } catch (error) {
    if (error instanceof ApiError || init?.signal?.aborted) throw error;
    throw new ApiError(0, controller.signal.aborted
      ? 'Сервер не ответил вовремя. Проверьте соединение и повторите запрос.'
      : 'Нет связи с сервером. Проверьте соединение и повторите запрос.');
  } finally {
    window.clearTimeout(timeout);
    init?.signal?.removeEventListener('abort', abort);
  }
}

async function csrf(signal?: AbortSignal): Promise<string> {
  const result = await request<{ csrf_token: string }>('auth/csrf/', { signal });
  csrfToken = result.csrf_token;
  return csrfToken;
}

function importedDataset(payload: any): Dataset {
  const counts = (value: unknown) => value !== null && typeof value === 'object' && !Array.isArray(value)
    && Object.values(value).every(count => typeof count === 'number' && Number.isFinite(count) && count >= 0);
  const summary = payload?.summary;
  if (!payload || typeof payload.id !== 'string' || typeof payload.name !== 'string'
    || typeof payload.customer_count !== 'number' || !Number.isFinite(payload.customer_count) || payload.customer_count < 0
    || typeof payload.imported_at !== 'string' || !Number.isFinite(Date.parse(payload.imported_at))
    || !summary || typeof summary.tariff_count !== 'number' || !Number.isFinite(summary.tariff_count)
    || !summary.segments || !['arpu_segment', 'data_segment', 'call_segment'].every(key => counts(summary.segments[key]))
    || !(summary.baseline_arpu === null || typeof summary.baseline_arpu === 'string' || typeof summary.baseline_arpu === 'number')
    || !(summary.synthetic === null || typeof summary.synthetic === 'boolean')
    || (summary.file_rows !== undefined && !counts(summary.file_rows))) {
    throw new ApiError(502, 'Сервер вернул неполные данные об импорте. Обновите страницу или повторите попытку.');
  }
  return payload as Dataset;
}

async function importDataset(files: File[], onProgress: (progress: ImportProgress) => void, signal?: AbortSignal): Promise<Dataset> {
  const token = await csrf(signal);
  if (signal?.aborted) throw new ApiError(0, 'Загрузка прервана. Повторите импорт.');
  const body = new FormData();
  files.forEach(file => body.append('files', file));
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.upload.addEventListener('progress', event => {
      onProgress({ phase: 'uploading', percent: event.lengthComputable
        ? Math.min(100, Math.round(event.loaded / event.total * 100)) : null });
    });
    xhr.upload.addEventListener('load', () => onProgress({ phase: 'processing' }));
    xhr.addEventListener('load', () => {
      let payload: any = null;
      try { payload = JSON.parse(xhr.responseText); } catch { /* Report invalid server responses below. */ }
      if (xhr.status < 200 || xhr.status >= 300) reject(responseError(xhr.status, payload));
      else if (!payload) reject(new ApiError(502, 'Сервер вернул некорректный ответ. Повторите попытку.'));
      else {
        try { resolve(importedDataset(payload)); } catch (error) { reject(error); }
      }
    });
    xhr.addEventListener('error', () => reject(new ApiError(0,
      'Соединение прервалось. Выбранные файлы сохранены — проверьте сеть и повторите импорт.')));
    xhr.addEventListener('abort', () => reject(new ApiError(0, 'Загрузка прервана. Повторите импорт.')));
    xhr.addEventListener('timeout', () => reject(new ApiError(0,
      'Сервер не ответил за пять минут. Обновите страницу, чтобы проверить импорт, или повторите попытку.')));
    xhr.open('POST', '/api/v1/datasets/import/');
    xhr.timeout = 5 * 60 * 1000;
    xhr.withCredentials = true;
    xhr.setRequestHeader('X-CSRFToken', token);
    const abort = () => xhr.abort();
    signal?.addEventListener('abort', abort, { once: true });
    xhr.addEventListener('loadend', () => signal?.removeEventListener('abort', abort));
    onProgress({ phase: 'uploading', percent: null });
    xhr.send(body);
  });
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
  importDemo: (signal?: AbortSignal) => request<Dataset>('datasets/import-demo/', { method: 'POST', body: '{}', signal }, false, 5 * 60 * 1000).then(importedDataset),
  importDataset,
  runs: (page = 1) => request<Page<ApiRun>>(`runs/?page=${page}`).then(page => ({ ...page, results: page.results.map(toRun) })),
  run: (id: string, signal?: AbortSignal) => request<ApiRun>(`runs/${id}/`, { signal }).then(toRun),
  createRun: (input: RunInput) => request<ApiRun>('runs/', {
    method: 'POST', body: JSON.stringify(input),
  }).then(toRun),
  startRun: (id: string, key: string, signal?: AbortSignal) => request<ApiRun>(`runs/${id}/start/`, {
    method: 'POST', body: '{}', headers: { 'Idempotency-Key': key }, signal,
  }).then(toRun),
  cancelRun: (id: string, signal?: AbortSignal) => request<ApiRun>(`runs/${id}/cancel/`, { method: 'POST', body: '{}', signal }).then(toRun),
  events: (id: string, after: number, signal?: AbortSignal) => request<RunEventsPage>(`runs/${id}/events/?after=${after}`, { signal }).then(page => ({ ...page, results: page.results.map(toEvent) })),
  results: (id: string, signal?: AbortSignal) => request<RunResult>(`runs/${id}/results/`, { signal }).then(toResults),
  exportRun: (id: string, signal?: AbortSignal) => request<Blob>(`runs/${id}/export/`, { signal }, true),
};
