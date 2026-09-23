import type { Dataset, Meta, Page, Run, RunEvent, RunInput, RunResults } from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string, public fields: Record<string, unknown> = {}) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit, csv = false): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  init?.signal?.addEventListener('abort', abort, { once: true });
  if (init?.signal?.aborted) controller.abort();
  const timeout = window.setTimeout(abort, 15000);
  const headers = new Headers(init?.headers);
  headers.set('Accept', csv ? 'text/csv' : 'application/json');
  if (init?.method === 'POST') {
    headers.set('Content-Type', 'application/json');
    const csrf = document.cookie.split('; ').find(value => value.startsWith('csrftoken='))?.slice(10);
    if (csrf) headers.set('X-CSRFToken', decodeURIComponent(csrf));
  }
  try {
    const response = await fetch(`/api/v1/${path}`, { ...init, headers, signal: controller.signal, credentials: 'same-origin' });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      const fallback = response.status === 403
        ? 'Запись недоступна. Проверьте доступ к приложению: публичный сайт пока работает только для чтения.'
        : 'Не удалось выполнить запрос. Попробуйте ещё раз.';
      throw new ApiError(response.status, payload?.error?.message || fallback, payload?.error?.fields || {});
    }
    if (csv) {
      if (!response.headers.get('Content-Type')?.toLowerCase().includes('text/csv')) {
        throw new ApiError(502, 'Вместо CSV сервер вернул другой формат. Повторите скачивание позже.');
      }
      return await response.blob() as T;
    }
    const payload = await response.json().catch(() => null);
    if (payload === null) throw new ApiError(502, 'Сервер вернул некорректный ответ.');
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

export const api = {
  meta: () => request<Meta>('meta/'),
  dataset: () => request<Dataset>('datasets/current/'),
  runs: (page = 1) => request<Page<Run>>(`runs/?page=${page}`),
  run: (id: string, signal?: AbortSignal) => request<Run>(`runs/${id}/`, { signal }),
  createRun: (input: RunInput) => request<Run>('runs/', {
    method: 'POST', body: JSON.stringify(input),
  }),
  startRun: (id: string, key: string, signal?: AbortSignal) => request<Run>(`runs/${id}/start/`, {
    method: 'POST', body: '{}', headers: { 'Idempotency-Key': key }, signal,
  }),
  cancelRun: (id: string, signal?: AbortSignal) => request<Run>(`runs/${id}/cancel/`, { method: 'POST', body: '{}', signal }),
  events: (id: string, after: number, signal?: AbortSignal) => request<Page<RunEvent>>(`runs/${id}/events/?after=${after}`, { signal }),
  results: (id: string, signal?: AbortSignal) => request<RunResults>(`runs/${id}/results/`, { signal }),
  exportRun: (id: string, signal?: AbortSignal) => request<Blob>(`runs/${id}/export/`, { signal }, true),
};
