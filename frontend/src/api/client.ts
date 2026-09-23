import type { Dataset, Meta, Page, Run, RunInput } from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string, public fields: Record<string, unknown> = {}) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1/${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, payload?.error?.message || 'Не удалось выполнить запрос.',
      payload?.error?.fields || {});
  }
  if (payload === null) throw new ApiError(502, 'Сервер вернул некорректный ответ.');
  return payload as T;
}

export const api = {
  meta: () => request<Meta>('meta/'),
  dataset: () => request<Dataset>('datasets/current/'),
  runs: (page = 1) => request<Page<Run>>(`runs/?page=${page}`),
  run: (id: string) => request<Run>(`runs/${id}/`),
  createRun: (input: RunInput) => request<Run>('runs/', {
    method: 'POST', body: JSON.stringify(input),
  }),
};
