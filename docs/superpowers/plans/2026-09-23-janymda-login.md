# Janymda Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a working Django-session login page that protects every frontend route and returns users to the page they requested.

**Architecture:** `src/api/client.ts` owns cookie and CSRF handling and exposes typed session methods. `App` owns the single session state, gates data loading, and redirects guests to `/login`. A standalone `LoginPage` owns only form state and presentation.

**Tech Stack:** React 18.2, React Router 7, TypeScript 5.7, Vite 6, Vitest and React Testing Library already being introduced by another developer in the shared worktree.

**Spec:** `docs/superpowers/specs/2026-09-23-janymda-login-design.md`

## Global Constraints

- Use the published `https://hack.1ge.kz/api/schema/` auth contract; local `main` backend does not yet expose auth routes.
- Use same-origin session cookies and `X-CSRFToken`; do not store passwords or CSRF tokens in localStorage or sessionStorage.
- Keep React at `18.2.0`; obtain all application data through `src/api/client.ts`.
- Preserve the existing responsive Janymda navigation and actual API data.
- Commit only files owned by this task; do not stage unrelated concurrent frontend changes.

## Review Focus

- Direct visit to `/runs/123` while logged out redirects to `/login` and returns to that exact local URL after login.
- Invalid credentials show the API error without setting authenticated state or retaining the password in storage.
- A stale CSRF token after login is replaced by the token returned by the login response before `POST runs/`.
- An expired session reported as `not_authenticated` returns to login; other `403` errors remain visible as permission errors.
- A network error during initial session check offers retry and does not mislabel the user as a guest.

---

### Task 1: Typed session and CSRF API client

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/api/client.auth.test.ts`

**Interfaces:**
- Produces `Session = { authenticated: boolean; user: { id: number; username: string; is_staff: boolean } | null; csrf_token?: string }`.
- Produces `api.me(): Promise<Session>`, `api.login(username: string, password: string): Promise<Session>`, `api.logout(): Promise<Session>`, `api.onUnauthorized(listener: () => void): () => void`.
- `ApiError` gains a `code: string` property while preserving `status`, `message`, and `fields`.

- [ ] **Step 1: Write failing tests.** Mock `global.fetch` in `client.auth.test.ts`. Assert `me()` uses `/api/v1/auth/me/` with `credentials: 'same-origin'`; `login()` calls `csrf/` before `login/`, sends JSON credentials and `X-CSRFToken`; a later `createRun()` uses the rotated `csrf_token` from login; `logout()` sends CSRF; only `{ error: { code: 'not_authenticated' } }` invokes the unauthorized listener. Include a rejected `fetch` for the initial check and an invalid-credentials response.

```ts
expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/auth/login/',
  expect.objectContaining({ method: 'POST', credentials: 'same-origin',
    headers: expect.objectContaining({ 'X-CSRFToken': 'csrf-before-login' }) }));
expect(onUnauthorized).not.toHaveBeenCalled();
```

- [ ] **Step 2: Run `npm --prefix frontend test -- src/api/client.auth.test.ts` and confirm the new tests fail for missing session methods.**
- [ ] **Step 3: Add the `Session` type and implement a single `request<T>` path.** Use `credentials: 'same-origin'`; keep a module-scoped CSRF token; call `GET auth/csrf/` when a POST has no current token; update the token from `Session.csrf_token`; parse `error.code`; notify listeners only when the code is `not_authenticated`.

```ts
let csrfToken: string | null = null;
const unauthorizedListeners = new Set<() => void>();
async function csrf(): Promise<string> {
  const result = await request<{ csrf_token: string }>('auth/csrf/');
  csrfToken = result.csrf_token;
  return csrfToken;
}
```

- [ ] **Step 4: Run the focused test and `npm --prefix frontend run typecheck`; fix failures in these files only.**
- [ ] **Step 5: Commit the client and its tests.** Use `Next: connect the login form and route guard.` in the commit body.

### Task 2: Standalone login form

**Files:**
- Create: `frontend/src/components/LoginPage.tsx`
- Create: `frontend/src/components/LoginPage.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes `api.login(username, password)` and `Session` from Task 1.
- Produces `<LoginPage onAuthenticated={(session: Session) => void} />`.

- [ ] **Step 1: Write failing component tests.** Render the form, assert the Janymda logo, accessible username and password fields, required-field validation, disabled submit while pending, API error text on rejected login, and `onAuthenticated(session)` exactly once on success. Assert the password field uses `type="password"` by default and no browser storage API is called.

```tsx
render(<LoginPage onAuthenticated={onAuthenticated} />);
await user.type(screen.getByLabelText('Логин'), 'analyst');
await user.type(screen.getByLabelText('Пароль'), 'wrong');
await user.click(screen.getByRole('button', { name: 'Войти' }));
expect(await screen.findByRole('alert')).toHaveTextContent('Неверный логин или пароль');
```

- [ ] **Step 2: Run `npm --prefix frontend test -- src/components/LoginPage.test.tsx` and confirm the missing component fails.**
- [ ] **Step 3: Implement the form.** Use the existing `/branding/janymda-logo.jpg`; a centered card with a concise heading, labels, password visibility control, submit button, and inline error. Keep all form state within `LoginPage`; clear the password after a failed attempt. Add responsive `login-*` CSS rules without changing existing dashboard styles.

```tsx
async function submit(event: FormEvent<HTMLFormElement>) {
  event.preventDefault();
  if (busy) return;
  setBusy(true); setError(null);
  try { onAuthenticated(await api.login(username, password)); }
  catch (cause) { setPassword(''); setError(cause); }
  finally { setBusy(false); }
}
```

- [ ] **Step 4: Run the focused test and `npm --prefix frontend run build`.**
- [ ] **Step 5: Commit the form and styles.** Use `Next: gate app routes and add logout.` in the commit body.

### Task 3: Session gate, return navigation, and logout

**Files:**
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/App.auth.test.tsx`

**Interfaces:**
- Consumes `api.me`, `api.logout`, `api.onUnauthorized`, `LoginPage`, and `Session`.
- Produces guest `/login` route, guarded dashboard shell, and accessible logout control.

- [ ] **Step 1: Write failing app tests.** Use `MemoryRouter` and mocked API methods. Cover direct `/runs/123` navigation when guest, successful login returning to `/runs/123`, authenticated reload opening the requested page, logout returning to `/login`, `not_authenticated` event clearing the session, unrelated `403` not clearing it, and failed `me()` showing a retry button.

```tsx
render(<MemoryRouter initialEntries={['/runs/123']}><App /></MemoryRouter>);
expect(await screen.findByRole('heading', { name: 'Вход в Janymda' })).toBeVisible();
expect(screen.queryByRole('navigation', { name: 'Основная навигация' })).not.toBeInTheDocument();
```

- [ ] **Step 2: Run `npm --prefix frontend test -- src/App.auth.test.tsx` and confirm failures for the missing login gate.**
- [ ] **Step 3: Implement the route gate in `App`.** Check `api.me()` once on mount; only load `meta` and `dataset` when authenticated. Redirect guests from any path other than `/login` with `<Navigate to="/login" replace state={{ from: location.pathname + location.search + location.hash }} />`. After login, navigate to `state.from` only if it begins with one `/` and not `//`, otherwise `/`. An authenticated visit to `/login` redirects to `/`. Show an error and retry button when `me()` rejects.

```tsx
const [session, setSession] = useState<Session | null | undefined>(undefined);
const [sessionError, setSessionError] = useState<unknown>(null);
useEffect(() => {
  let active = true;
  api.me().then(value => { if (active) setSession(value); })
    .catch(cause => { if (active) setSessionError(cause); });
  return () => { active = false; };
}, [retry]);
if (sessionError) return <div role="alert">Не удалось проверить вход. <button onClick={() => { setSessionError(null); setRetry(n => n + 1); }}>Повторить</button></div>;
if (session === undefined) return <p role="status">Проверяем вход…</p>;
```

- [ ] **Step 4: Implement logout and session expiration.** Render the username and a logout button in the authenticated shell; call `api.logout()` while disabled; on success clear `session`, `meta`, and `dataset`, then navigate to `/login`. Subscribe to `api.onUnauthorized` and clear session only for that event. Keep logout available at widths below 760px.
- [ ] **Step 5: Run focused tests, `npm --prefix frontend run build`, and `make check`.** Verify no `GET meta/` is sent before authentication.
- [ ] **Step 6: Inspect the login page at desktop and 390px mobile width.** Use browser screenshots and check keyboard labels, errors, button state, return navigation, and no horizontal overflow. The local backend may return 404 for auth routes; use mocked responses for interaction checks and report the backend mismatch.
- [ ] **Step 7: Commit the app integration and tests.** Review `git status` first and stage only this task's files.

### Task 4: Final branch review

**Files:** None beyond fixes justified by review.

- [ ] **Step 1: Compare the final diff with the spec and review the five Review Focus cases.**
- [ ] **Step 2: Run `npm --prefix frontend test`, `make check`, and `git diff --check` with exit code 0.**
- [ ] **Step 3: Review staged files for unrelated edits and commit any verified fixes.**
