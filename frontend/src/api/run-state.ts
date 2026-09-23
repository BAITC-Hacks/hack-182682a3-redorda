const startKeys = new Map<string, string>();

export function startKeyForRun(id: string): string {
  if (startKeys.has(id)) return startKeys.get(id)!;
  const storageKey = `redorda:start:${id}`;
  let key: string | null = null;
  try { key = sessionStorage.getItem(storageKey); } catch { /* Storage can be disabled. */ }
  key ||= crypto.randomUUID();
  startKeys.set(id, key);
  try { sessionStorage.setItem(storageKey, key); } catch { /* Keep the key for this page. */ }
  return key;
}

export function mergeEvents<T extends { id: number }>(previous: T[], incoming: T[]): T[] {
  return [...new Map([...previous, ...incoming].map(event => [event.id, event])).values()]
    .sort((left, right) => left.id - right.id);
}

export function nextEventCursor(after: number, page: { next_after: number; has_more: boolean; results: { id: number }[] }): number {
  if (!Number.isSafeInteger(page.next_after) || page.next_after < after
    || (page.has_more && page.next_after <= after)
    || page.results.some(event => !Number.isSafeInteger(event.id)
      || event.id <= after || event.id > page.next_after)) {
    throw new Error('Сервер вернул некорректную страницу событий. Обновите состояние.');
  }
  return page.next_after;
}
