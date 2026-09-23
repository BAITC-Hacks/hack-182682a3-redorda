import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { Run, RunEvent, RunResults } from '../api/types';
import { isActive } from './presentation';

export function useRun(id: string, execution: boolean) {
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [results, setResults] = useState<RunResults | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const cursor = useRef(0);
  const lastRun = useRef<Run | null>(null);
  const retry = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    let latest: Run | null = lastRun.current;
    const message = (reason: unknown) => reason instanceof Error ? reason.message : 'Проверьте соединение.';

    async function refresh() {
      const failures: string[] = [];
      try {
        latest = await api.run(id, controller.signal);
        if (controller.signal.aborted) return;
        lastRun.current = latest;
        setRun(latest);
      } catch (reason) {
        failures.push(`Не удалось обновить план. ${message(reason)}`);
      }
      if (controller.signal.aborted) return;
      if (latest && latest.status !== 'draft' && execution) {
        // Drain every page, including final events after the run finishes.
        try {
          let more = true;
          while (more && !controller.signal.aborted) {
            const page = await api.events(id, cursor.current, controller.signal);
            if (controller.signal.aborted) return;
            const nextCursor = Math.max(cursor.current, ...page.results.map(event => event.id));
            if (page.next && nextCursor <= cursor.current) throw new Error('Не удалось загрузить следующую страницу журнала.');
            cursor.current = nextCursor;
            setEvents(previous => Array.from(new Map([...previous, ...page.results].map(event => [event.id, event])).values()).sort((a, b) => a.id - b.id));
            more = page.next !== null;
          }
        } catch (reason) { failures.push(`Не удалось обновить журнал. ${message(reason)}`); }
      }
      if (latest?.status === 'completed' && execution) {
        try {
          const value = await api.results(id, controller.signal);
          if (!controller.signal.aborted) setResults(value);
        } catch (reason) { failures.push(`Не удалось получить результат. ${message(reason)}`); }
      }
      if (controller.signal.aborted) return;
      setError(failures.length ? failures.join(' ') : null);
      setLoading(false);
      if (latest && isActive(latest)) timer = window.setTimeout(refresh, 2000);
    }
    setLoading(true);
    void refresh();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [id, execution, revision]);

  // The parent keys this hook's component by run ID, clearing its state on navigation.
  const accept = (value: Run) => { lastRun.current = value; setRun(value); retry(); };
  return { run, events, results, error, loading, retry, accept };
}
