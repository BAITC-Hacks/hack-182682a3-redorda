import { beforeEach, describe, expect, it, vi } from 'vitest';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

describe('session API', () => {
  it('checks the session using same-origin credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ authenticated: false, user: null }));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('./client');

    await expect(api.me()).resolves.toEqual({ authenticated: false, user: null });
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/me/',
      expect.objectContaining({ credentials: 'same-origin' }));
  });

  it('uses CSRF for login and the rotated token for later writes', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ csrf_token: 'csrf-before-login' }))
      .mockResolvedValueOnce(json({ authenticated: true,
        user: { id: 7, username: 'analyst', is_staff: false }, csrf_token: 'csrf-after-login' }))
      .mockResolvedValueOnce(json({ id: 'run-1' }));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('./client');

    await api.login('analyst', 'secret');
    await api.createRun({ name: 'October', budget: '100', max_contacts: 10,
      max_pilots: 1, seed: 42, strategy: 'baseline' });

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/v1/auth/csrf/',
      expect.objectContaining({ credentials: 'same-origin' }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/auth/login/',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin',
        body: JSON.stringify({ username: 'analyst', password: 'secret' }) }));
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get('X-CSRFToken')).toBe('csrf-before-login');
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/v1/runs/',
      expect.objectContaining({ method: 'POST' }));
    expect(new Headers(fetchMock.mock.calls[2][1].headers).get('X-CSRFToken')).toBe('csrf-after-login');
  });

  it('fetches CSRF and sends it when logging out', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ csrf_token: 'logout-token' }))
      .mockResolvedValueOnce(json({ authenticated: false, user: null, csrf_token: 'new-token' }));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('./client');

    await expect(api.logout()).resolves.toMatchObject({ authenticated: false });
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/auth/logout/',
      expect.objectContaining({ method: 'POST' }));
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get('X-CSRFToken')).toBe('logout-token');
  });

  it('notifies only when the API reports an expired session', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ error: { code: 'permission_denied', message: 'Нет доступа', fields: {} } }, 403))
      .mockResolvedValueOnce(json({ error: { code: 'not_authenticated', message: 'Войдите', fields: {} } }, 403));
    vi.stubGlobal('fetch', fetchMock);
    const { api, ApiError } = await import('./client');
    const onUnauthorized = vi.fn();
    api.onUnauthorized(onUnauthorized);

    await expect(api.meta()).rejects.toMatchObject({ code: 'permission_denied' });
    expect(onUnauthorized).not.toHaveBeenCalled();
    await expect(api.meta()).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it('preserves invalid credential errors and network failures', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ csrf_token: 'login-token' }))
      .mockResolvedValueOnce(json({ error: { code: 'invalid_credentials', message: 'Неверный логин или пароль', fields: {} } }, 401))
      .mockRejectedValueOnce(new TypeError('Network down'));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('./client');

    await expect(api.login('analyst', 'bad')).rejects.toMatchObject({
      status: 401, code: 'invalid_credentials', message: 'Неверный логин или пароль',
    });
    await expect(api.me()).rejects.toMatchObject({ status: 0, message: expect.stringContaining('Нет связи с сервером') });
  });
});

it('allows a demo import to finish after the normal request deadline', async () => {
  vi.useFakeTimers();
  let importSignal: AbortSignal | undefined;
  let finish!: (response: Response) => void;
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(json({ csrf_token: 'import-token' }))
    .mockImplementationOnce((_url: string, init: RequestInit) => {
      importSignal = init.signal ?? undefined;
      return new Promise<Response>(resolve => { finish = resolve; });
    });
  vi.stubGlobal('fetch', fetchMock);
  const { api } = await import('./client');
  const request = api.importDemo();
  await vi.advanceTimersByTimeAsync(16000);
  expect(importSignal?.aborted).toBe(false);
  finish(json({ id: 'demo', name: 'Demo', customer_count: 1,
    imported_at: '2026-09-23T10:00:00Z', summary: { tariff_count: 1,
      baseline_arpu: null, synthetic: true,
      segments: { arpu_segment: {}, data_segment: {}, call_segment: {} } } }));
  await expect(request).resolves.toMatchObject({ id: 'demo' });
});
