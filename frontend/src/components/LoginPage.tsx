import { useState, type FormEvent } from 'react';
import { ArrowRight, Eye, EyeOff } from 'lucide-react';
import { api } from '../api/client';
import type { Session } from '../api/types';

export default function LoginPage({ onAuthenticated }: { onAuthenticated: (session: Session) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const session = await api.login(username, password);
      if (!session.authenticated || !session.user) throw new Error('Не удалось войти. Повторите попытку.');
      onAuthenticated(session);
    } catch (cause) {
      setPassword('');
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  return <div className="login-page">
    <section className="login-intro" aria-label="О сервисе">
      <div className="login-brand"><img src="/branding/janymda-logo.jpg" alt="Janymda" /><strong>Janymda</strong></div>
      <div className="login-intro-copy"><span>Рабочее пространство</span>
        <p>Данные аудитории и планы кампаний в одном месте.</p>
      </div>
      <small>Beeline · HackAlem AI</small>
    </section>
    <main className="login-main">
      <div className="login-panel">
        <span className="login-kicker">Для команды проекта</span>
        <h1>Вход в Janymda</h1>
        <p>Введите учётные данные, чтобы открыть рабочее пространство.</p>
        <form onSubmit={submit}>
          <label htmlFor="login-username">Логин</label>
          <input id="login-username" name="username" autoComplete="username" value={username}
            onChange={event => setUsername(event.target.value)} maxLength={150} required />
          <label htmlFor="login-password">Пароль</label>
          <div className="login-password">
            <input id="login-password" name="password" type={visible ? 'text' : 'password'}
              autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)}
              maxLength={512} required />
            <button type="button" onClick={() => setVisible(value => !value)}
              aria-label={visible ? 'Скрыть пароль' : 'Показать пароль'}>
              {visible ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </div>
          {error !== null && <div className="login-error" role="alert">{error instanceof Error ? error.message : 'Не удалось войти. Повторите попытку.'}</div>}
          <button className="login-submit" type="submit" disabled={busy}>
            {busy ? 'Входим…' : 'Войти'}<ArrowRight size={18} />
          </button>
        </form>
      </div>
    </main>
  </div>;
}
