import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import LoginPage from './LoginPage';
import { api } from '../api/client';

vi.mock('../api/client', () => ({ api: { login: vi.fn() } }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const session = { authenticated: true,
  user: { id: 7, username: 'analyst', is_staff: false }, csrf_token: 'rotated' };

it('shows the Janymda form and requires both credentials', async () => {
  const user = userEvent.setup();
  render(<LoginPage onAuthenticated={vi.fn()} />);

  expect(screen.getByRole('heading', { name: 'Вход в Janymda' })).toBeVisible();
  expect(screen.getByRole('img', { name: 'Janymda' })).toHaveAttribute('src', '/branding/janymda-logo.jpg');
  expect(screen.getByLabelText('Логин')).toBeRequired();
  expect(screen.getByLabelText('Пароль')).toBeRequired();
  expect(screen.getByLabelText('Пароль')).toHaveAttribute('type', 'password');
  await user.click(screen.getByRole('button', { name: 'Войти' }));
  expect(api.login).not.toHaveBeenCalled();
});

it('submits once, disables the form while pending and never stores credentials', async () => {
  const user = userEvent.setup();
  let resolveLogin!: (value: typeof session) => void;
  vi.mocked(api.login).mockReturnValue(new Promise(resolve => { resolveLogin = resolve; }));
  const storageWrite = vi.spyOn(Storage.prototype, 'setItem');
  const onAuthenticated = vi.fn();
  render(<LoginPage onAuthenticated={onAuthenticated} />);

  await user.type(screen.getByLabelText('Логин'), 'analyst');
  await user.type(screen.getByLabelText('Пароль'), 'secret');
  await user.click(screen.getByRole('button', { name: 'Войти' }));
  expect(screen.getByRole('button', { name: 'Входим…' })).toBeDisabled();
  expect(api.login).toHaveBeenCalledOnce();
  resolveLogin(session);
  await waitFor(() => expect(onAuthenticated).toHaveBeenCalledWith(session));
  expect(onAuthenticated).toHaveBeenCalledOnce();
  expect(storageWrite).not.toHaveBeenCalled();
  storageWrite.mockRestore();
});

it('shows invalid credentials and clears the password for retry', async () => {
  const user = userEvent.setup();
  vi.mocked(api.login).mockRejectedValue(new Error('Неверный логин или пароль'));
  render(<LoginPage onAuthenticated={vi.fn()} />);

  await user.type(screen.getByLabelText('Логин'), 'analyst');
  await user.type(screen.getByLabelText('Пароль'), 'wrong');
  await user.click(screen.getByRole('button', { name: 'Войти' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Неверный логин или пароль');
  expect(screen.getByLabelText('Пароль')).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Войти' })).toBeEnabled();
});

it('lets the user reveal and hide the password', async () => {
  const user = userEvent.setup();
  render(<LoginPage onAuthenticated={vi.fn()} />);
  await user.click(screen.getByRole('button', { name: 'Показать пароль' }));
  expect(screen.getByLabelText('Пароль')).toHaveAttribute('type', 'text');
  await user.click(screen.getByRole('button', { name: 'Скрыть пароль' }));
  expect(screen.getByLabelText('Пароль')).toHaveAttribute('type', 'password');
});
