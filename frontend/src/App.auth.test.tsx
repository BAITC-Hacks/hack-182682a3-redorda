import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { api, ApiError } from './api/client';

vi.mock('./api/client', async importOriginal => {
  const original = await importOriginal<typeof import('./api/client')>();
  return { ...original, api: {
    me: vi.fn(), login: vi.fn(), logout: vi.fn(), onUnauthorized: vi.fn(),
    meta: vi.fn(), dataset: vi.fn(), runs: vi.fn(), run: vi.fn(), createRun: vi.fn(),
  } };
});

const guest = { authenticated: false, user: null };
const member = { authenticated: true,
  user: { id: 7, username: 'analyst', is_staff: false }, csrf_token: 'csrf' };
const meta = { limits: { budget: 100000, contacts: 15000, pilots: 20 },
  channel_costs: {}, features: { run_execution: false, openai_strategy: false, csv_export: false } };

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location" data-from={(location.state as { from?: string } | null)?.from || ''}>
    {location.pathname + location.search + location.hash}</output>;
}

function open(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><App /><LocationProbe /></MemoryRouter>);
}

beforeEach(() => {
  vi.mocked(api.me).mockResolvedValue(guest);
  vi.mocked(api.login).mockResolvedValue(member);
  vi.mocked(api.logout).mockResolvedValue(guest);
  vi.mocked(api.meta).mockResolvedValue(meta);
  vi.mocked(api.dataset).mockResolvedValue(null as never);
  vi.mocked(api.run).mockResolvedValue({ id: '123', name: 'План 123', status: 'draft',
    budget: '100', max_contacts: 10, max_pilots: 1, created_at: '2026-09-23T10:00:00Z' } as never);
  vi.mocked(api.onUnauthorized).mockReturnValue(() => {});
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it('redirects a guest deep link to login without loading dashboard data', async () => {
  open('/runs/123?view=full#details');
  expect(await screen.findByRole('heading', { name: 'Вход в Janymda' })).toBeVisible();
  expect(screen.getByTestId('location')).toHaveTextContent('/login');
  expect(screen.queryByRole('navigation', { name: 'Основная навигация' })).not.toBeInTheDocument();
  expect(api.meta).not.toHaveBeenCalled();
});

it('returns to the requested local URL after login', async () => {
  const user = userEvent.setup();
  open('/runs/123?view=full#details');
  await screen.findByRole('heading', { name: 'Вход в Janymda' });
  expect(screen.getByTestId('location')).toHaveAttribute('data-from', '/runs/123?view=full#details');
  await user.type(screen.getByLabelText('Логин'), 'analyst');
  await user.type(screen.getByLabelText('Пароль'), 'secret');
  await user.click(screen.getByRole('button', { name: 'Войти' }));

  expect(await screen.findByRole('heading', { name: 'План 123' })).toBeVisible();
  expect(screen.getByTestId('location')).toHaveTextContent('/runs/123?view=full#details');
  expect(api.login).toHaveBeenCalledWith('analyst', 'secret');
});

it('opens a protected page when the existing session is valid', async () => {
  vi.mocked(api.me).mockResolvedValue(member);
  open('/data');
  expect(await screen.findByRole('heading', { name: 'Данные для решений' })).toBeVisible();
  expect(screen.getByTestId('location')).toHaveTextContent('/data');
  expect(api.meta).toHaveBeenCalledOnce();
});

it('ends the session and returns to login', async () => {
  const user = userEvent.setup();
  vi.mocked(api.me).mockResolvedValue(member);
  open('/data');
  await screen.findByRole('heading', { name: 'Данные для решений' });
  await user.click(screen.getByRole('button', { name: 'Выйти' }));
  expect(await screen.findByRole('heading', { name: 'Вход в Janymda' })).toBeVisible();
  expect(screen.getByTestId('location')).toHaveTextContent('/login');
  expect(api.logout).toHaveBeenCalledOnce();
});

it('returns to login when a protected request reports an expired session', async () => {
  let expire!: () => void;
  vi.mocked(api.onUnauthorized).mockImplementation(listener => { expire = listener; return () => {}; });
  vi.mocked(api.me).mockResolvedValue(member);
  open('/data');
  await screen.findByRole('heading', { name: 'Данные для решений' });
  act(() => expire());
  expect(await screen.findByRole('heading', { name: 'Вход в Janymda' })).toBeVisible();
});

it('keeps the session for unrelated permission errors', async () => {
  vi.mocked(api.me).mockResolvedValue(member);
  vi.mocked(api.meta).mockRejectedValue(new ApiError(403, 'Нет доступа', {}, 'permission_denied'));
  open('/data');
  expect(await screen.findByRole('alert')).toHaveTextContent('Нет доступа');
  expect(screen.getByRole('navigation', { name: 'Основная навигация' })).toBeVisible();
  expect(screen.getByTestId('location')).toHaveTextContent('/data');
});

it('retries a failed session check without treating the user as a guest', async () => {
  const user = userEvent.setup();
  vi.mocked(api.me).mockRejectedValueOnce(new TypeError('Network down')).mockResolvedValueOnce(guest);
  open('/data');
  expect(await screen.findByRole('alert')).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'Вход в Janymda' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Повторить' }));
  await waitFor(() => expect(screen.getByRole('heading', { name: 'Вход в Janymda' })).toBeVisible());
});
