import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}#${crypto.randomUUID()}`);
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockFetch(t, replies) {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, ...init });
    assert.ok(replies.length, `Unexpected request to ${url}`);
    const reply = replies.shift();
    if (reply instanceof Error) throw reply;
    return reply;
  };
  t.after(() => { globalThis.fetch = original; });
  return calls;
}

test('login rotates CSRF and subsequent writes include session credentials', async t => {
  const { api } = await loadSource('../src/api/client.ts');
  const calls = mockFetch(t, [json({ csrf_token: 'before-login' }),
    json({ authenticated: true, user: { id: 1, username: 'tester', is_staff: false }, csrf_token: 'after-login' }),
    json({ id: 'run-a' })]);
  await api.login('tester', 'test-only-password');
  await api.createRun({ name: 'Run', budget: '100000', max_contacts: 15000, max_pilots: 20, seed: 42, strategy: 'openai' });
  assert.deepEqual(calls.map(call => call.url), ['/api/v1/auth/csrf/', '/api/v1/auth/login/', '/api/v1/runs/']);
  assert.equal(calls[1].headers.get('X-CSRFToken'), 'before-login');
  assert.equal(calls[2].headers.get('X-CSRFToken'), 'after-login');
  assert.equal(JSON.parse(calls[2].body).strategy, 'openai');
  assert.ok(calls.every(call => call.credentials === 'include'));
});

test('ambiguous start failure is never retried automatically', async t => {
  const { api } = await loadSource('../src/api/client.ts');
  const calls = mockFetch(t, [json({ csrf_token: 'csrf' }), new TypeError('Network interrupted')]);
  await assert.rejects(api.startRun('run-a', 'stable-key'), /Network interrupted/);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].method, 'POST');
  assert.equal(calls[1].headers.get('Idempotency-Key'), 'stable-key');
  assert.equal(calls[1].headers.get('X-CSRFToken'), 'csrf');
});

test('missing CSRF prevents the write', async t => {
  const { api } = await loadSource('../src/api/client.ts');
  const calls = mockFetch(t, [json({})]);
  await assert.rejects(api.cancelRun('run-a'), /CSRF/);
  assert.equal(calls.length, 1);
});

test('protected 403 expires the session and next login refreshes CSRF', async t => {
  const { api, onAuthenticationRequired } = await loadSource('../src/api/client.ts');
  let required = 0;
  const unsubscribe = onAuthenticationRequired(() => { required += 1; });
  const calls = mockFetch(t, [json({ error: { code: 'not_authenticated', message: 'Войдите' } }, 403),
    json({ csrf_token: 'fresh-token' }), json({ authenticated: true, user: null, csrf_token: 'rotated-token' })]);
  await assert.rejects(api.run('run-a'), error => error.status === 403 && error.code === 'not_authenticated');
  assert.equal(required, 1);
  await api.login('tester', 'test-only-password');
  assert.equal(calls[2].headers.get('X-CSRFToken'), 'fresh-token');
  unsubscribe();
});

test('invalid login stays on the login form without broadcasting session expiry', async t => {
  const { api, onAuthenticationRequired } = await loadSource('../src/api/client.ts');
  let required = 0;
  onAuthenticationRequired(() => { required += 1; });
  mockFetch(t, [json({ csrf_token: 'csrf' }), json({ error: { code: 'invalid_credentials', message: 'Неверный пароль' } }, 401)]);
  await assert.rejects(api.login('tester', 'incorrect'), error => error.code === 'invalid_credentials');
  assert.equal(required, 0);
});

test('CSV export preserves error responses and uses the authenticated download', async t => {
  const { api } = await loadSource('../src/api/client.ts');
  const calls = mockFetch(t, [json({ error: { code: 'result_not_ready', message: 'Результат не готов' } }, 409),
    new Response('campaign_name,channel\nexample,push\n', { headers: { 'Content-Type': 'text/csv' } })]);
  await assert.rejects(api.exportRun('run-a'), error => error.code === 'result_not_ready');
  const csv = await api.exportRun('run-a');
  assert.equal(await csv.text(), 'campaign_name,channel\nexample,push\n');
  assert.ok(calls.every(call => call.credentials === 'include'));
  assert.ok(calls.every(call => new Headers(call.headers).get('Accept').includes('application/json')));
});

test('event cursor rejects a stalled or inconsistent page and preserves empty-page cursor', async () => {
  const { nextEventCursor } = await loadSource('../src/api/run-state.ts');
  assert.equal(nextEventCursor(12, { results: [], next_after: 12, has_more: false }), 12);
  assert.throws(() => nextEventCursor(12, { results: [], next_after: 12, has_more: true }), /событий/);
  assert.throws(() => nextEventCursor(12, { results: [{ id: 14 }], next_after: 13, has_more: false }), /событий/);
});

test('event retries deduplicate and retain ordered pilot observations', async () => {
  const { mergeEvents } = await loadSource('../src/api/run-state.ts');
  const previous = [{ id: 3, kind: 'pilot_completed' }, { id: 2, kind: 'running' }];
  const result = mergeEvents(previous, [{ id: 3, kind: 'pilot_completed' }, { id: 4, kind: 'completed' }]);
  assert.deepEqual(result.map(event => event.id), [2, 3, 4]);
  assert.equal(result.filter(event => event.kind === 'pilot_completed').length, 1);
});

test('idempotency key survives reload and remains stable without browser storage', async t => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage');
  const values = new Map();
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: {
    getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value),
  } });
  t.after(() => {
    if (original) Object.defineProperty(globalThis, 'sessionStorage', original);
    else delete globalThis.sessionStorage;
  });
  const firstPage = await loadSource('../src/api/run-state.ts');
  const key = firstPage.startKeyForRun('run-a');
  const reloadedPage = await loadSource('../src/api/run-state.ts');
  assert.equal(reloadedPage.startKeyForRun('run-a'), key);
  assert.notEqual(reloadedPage.startKeyForRun('run-b'), key);
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, get() { throw new Error('Unavailable'); } });
  assert.equal(firstPage.startKeyForRun('run-c'), firstPage.startKeyForRun('run-c'));
});

test('team commands use the saved snapshot, CSRF, and idempotency key', async t => {
  const { api } = await loadSource('../src/api/client.ts');
  const calls = mockFetch(t, [json({ schema_version: 1, snapshot_id: 'snapshot-a', tasks: [], artifacts: [], available_commands: ['compare'] }),
    json({ csrf_token: 'csrf' }), json({ id: 'command-a', type: 'compare', status: 'queued', result: null, error: null }),
    json({ id: 'command-a', type: 'compare', status: 'completed', result: { artifact_id: 'artifact-a' }, error: null })]);
  const snapshot = await api.team('run-a');
  await api.command('run-a', 'compare', snapshot.snapshot_id, { constraints: { budget: '50000' } }, 'stable-command-key');
  await api.commandResult('run-a', 'command-a');
  assert.deepEqual(calls.map(call => call.url), ['/api/v1/runs/run-a/team/', '/api/v1/auth/csrf/',
    '/api/v1/runs/run-a/commands/', '/api/v1/runs/run-a/commands/command-a/']);
  assert.equal(calls[2].headers.get('Idempotency-Key'), 'stable-command-key');
  assert.equal(calls[2].headers.get('X-CSRFToken'), 'csrf');
  assert.deepEqual(JSON.parse(calls[2].body), { type: 'compare', snapshot_id: 'snapshot-a',
    parameters: { constraints: { budget: '50000' } } });
  assert.ok(calls.every(call => call.credentials === 'include'));
});

test('text requests prepare only known typed commands', async () => {
  const { parseTeamRequest } = await loadSource('../src/api/team-requests.ts');
  assert.deepEqual(parseTeamRequest('объясни campaign-7'), { type: 'explain', campaignId: 'campaign-7' });
  assert.deepEqual(parseTeamRequest('сравни бюджет 50000,50'), { type: 'compare', budget: '50000.50' });
  assert.deepEqual(parseTeamRequest('создай план Новый бюджет'), { type: 'create_plan', planName: 'Новый бюджет' });
  assert.equal(parseTeamRequest('запусти пилот'), null);
});
