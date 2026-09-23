import type { Dataset, Meta, Page, Run, RunInput } from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string, public fields: Record<string, unknown> = {}, public code?: string) {
    super(message);
  }
}

export type ImportProgress = { phase: 'uploading'; percent: number | null } | { phase: 'processing' };

function responseError(status: number, payload: any) {
  return new ApiError(status, payload?.error?.message || 'Не удалось выполнить запрос. Повторите попытку.',
    payload?.error?.fields || {}, payload?.error?.code);
}

async function csrfToken(signal?: AbortSignal): Promise<string> {
  const payload = await request<{ csrf_token: string }>('auth/csrf/', { signal });
  if (!payload.csrf_token) throw new ApiError(502, 'Не удалось подготовить защищённый запрос. Повторите попытку.');
  return payload.csrf_token;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('Content-Type', 'application/json');
  if (init?.method && !['GET', 'HEAD', 'OPTIONS'].includes(init.method.toUpperCase())) {
    headers.set('X-CSRFToken', await csrfToken(init.signal ?? undefined));
  }
  let response: Response;
  try {
    response = await fetch(`/api/v1/${path}`, { ...init, credentials: 'include', headers });
  } catch {
    throw new ApiError(0, 'Нет соединения с сервером. Проверьте подключение и повторите попытку.');
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw responseError(response.status, payload);
  }
  if (payload === null) throw new ApiError(502, 'Сервер вернул некорректный ответ.');
  return payload as T;
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
  const token = await csrfToken(signal);
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
  meta: () => request<Meta>('meta/'),
  dataset: () => request<Dataset>('datasets/current/'),
  importDemo: (signal?: AbortSignal) => request<Dataset>('datasets/import-demo/', { method: 'POST', body: '{}', signal }).then(importedDataset),
  importDataset,
  runs: (page = 1) => request<Page<Run>>(`runs/?page=${page}`),
  run: (id: string) => request<Run>(`runs/${id}/`),
  createRun: (input: RunInput) => request<Run>('runs/', {
    method: 'POST', body: JSON.stringify(input),
  }),
};
