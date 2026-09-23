import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { api, ApiError } from '../api/client';
import { mergeEvents, nextEventCursor, startKeyForRun } from '../api/run-state';
import type { CommandType, Run, RunEvent, RunResults, TeamCommand, TeamSnapshot } from '../api/types';

type RunAction = 'start' | 'cancel' | 'export';
interface RunState {
  selectedId: string | null; select: (id: string | null) => void;
  run: Run | null; events: RunEvent[]; result: RunResults | null; team: TeamSnapshot | null;
  teamUnavailable: boolean; liveHandoff: RunEvent | null; loadError: unknown; actionError: unknown;
  action: RunAction | null; refresh: () => void; perform: (kind: RunAction) => Promise<void>;
  command: TeamCommand | null; commandError: unknown; sendCommand: (type: CommandType, parameters: Record<string, unknown>) => Promise<void>;
}
const Context = createContext<RunState | null>(null);
const terminal = new Set<Run['status']>(['completed', 'failed', 'cancelled']);
const selectionKey = 'redorda:selected-run';
function savedId(): string | null { try { return localStorage.getItem(selectionKey); } catch { return null; } }

export function RunProvider({ children, active }: { children: ReactNode; active: boolean }) {
  const [selectedId, setSelectedId] = useState<string | null>(savedId);
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [result, setResult] = useState<RunResults | null>(null);
  const [team, setTeam] = useState<TeamSnapshot | null>(null);
  const [teamUnavailable, setTeamUnavailable] = useState(false);
  const [liveHandoff, setLiveHandoff] = useState<RunEvent | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [action, setAction] = useState<RunAction | null>(null);
  const [command, setCommand] = useState<TeamCommand | null>(null);
  const [commandError, setCommandError] = useState<unknown>(null);
  const [revision, setRevision] = useState(0);
  const pending = useRef(false);
  const cursor = useRef(0);
  const baseline = useRef(0);
  const hydrated = useRef(false);
  const teamUnsupported = useRef(false);
  const historyCaughtUp = useRef(false);
  const resultLoaded = useRef(false);
  const commandRef = useRef<TeamCommand | null>(null);

  const select = useCallback((id: string | null) => {
    setSelectedId(id);
    try { if (id) localStorage.setItem(selectionKey, id); else localStorage.removeItem(selectionKey); } catch { /* Storage is optional. */ }
  }, []);
  const refresh = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    cursor.current = 0; baseline.current = 0; hydrated.current = false; teamUnsupported.current = false;
    historyCaughtUp.current = false; resultLoaded.current = false;
    setRun(null); setEvents([]); setResult(null); setTeam(null); setTeamUnavailable(false);
    setLiveHandoff(null); setLoadError(null); setActionError(null); setCommand(null); setCommandError(null);
    commandRef.current = null;
  }, [selectedId, active]);

  useEffect(() => {
    if (!active || !selectedId) return;
    const id = selectedId;
    const controller = new AbortController();
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        if (!hydrated.current) {
          try {
            const snapshot = await api.team(id, controller.signal);
            if (!alive) return;
            setTeam(snapshot); baseline.current = snapshot.last_event_id;
          } catch (error) {
            if (!alive || controller.signal.aborted) return;
            teamUnsupported.current = !(error instanceof ApiError) || [404, 405, 501].includes(error.status);
            setTeamUnavailable(true);
          }
          hydrated.current = true;
        }
        const current = await api.run(id, controller.signal);
        if (!alive) return;
        setRun(previous => previous?.cancellation_requested && !terminal.has(current.status)
          ? { ...current, cancellation_requested: true } : current);
        let more = false;
        let journalError: unknown = null;
        if (current.status !== 'draft') {
          try {
            for (let pageNumber = 0; pageNumber < 5; pageNumber += 1) {
              const priorCursor = cursor.current;
              const page = await api.events(id, priorCursor, controller.signal);
              if (!alive) return;
              cursor.current = nextEventCursor(priorCursor, page);
              setEvents(previous => mergeEvents(previous, page.results));
              const fresh = historyCaughtUp.current ? page.results.filter(event => event.id > baseline.current && event.id > priorCursor) : [];
              const handoff = fresh.filter(event => event.event_kind === 'task_handoff').at(-1);
              if (handoff) setLiveHandoff(handoff);
              more = page.has_more;
              if (!more) break;
            }
            if (!more) historyCaughtUp.current = true;
          } catch (error) {
            if (!alive || controller.signal.aborted) return;
            journalError = error;
          }
        }
        if (current.status === 'completed' && !resultLoaded.current) {
          const output = await api.results(id, controller.signal);
          if (!alive) return;
          setResult(output); resultLoaded.current = true;
        }
        if (!teamUnsupported.current) {
          try { const snapshot = await api.team(id, controller.signal); if (alive) { setTeam(snapshot); setTeamUnavailable(false); } }
          catch (error) {
            if (!alive || controller.signal.aborted) return;
            teamUnsupported.current = !(error instanceof ApiError) || [404, 405, 501].includes(error.status);
            setTeamUnavailable(true);
          }
        }
        const pendingCommand = commandRef.current;
        if (pendingCommand && ['queued', 'running'].includes(pendingCommand.status)) {
          const latest = await api.commandResult(id, pendingCommand.id, controller.signal);
          if (alive) {
            commandRef.current = latest; setCommand(latest);
            if (latest.status === 'completed') {
              const snapshot = await api.team(id, controller.signal);
              if (alive) setTeam(snapshot);
            }
          }
        }
        if (alive) {
          setLoadError(journalError);
          if (more || ['queued', 'running'].includes(current.status) || commandRef.current && ['queued', 'running'].includes(commandRef.current.status)) timer = setTimeout(poll, 2000);
        }
      } catch (error) {
        if (alive && !controller.signal.aborted) {
          const message = error instanceof Error ? error.message : String(error);
          setLoadError(new Error(`Не удалось обновить план. ${message}`));
          timer = setTimeout(poll, 2000);
        }
      }
    }
    void poll();
    return () => { alive = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [selectedId, active, revision]);

  async function perform(kind: RunAction) {
    if (!selectedId || pending.current) return;
    pending.current = true; setAction(kind); setActionError(null);
    try {
      if (kind === 'export') {
        const blob = await api.exportRun(selectedId);
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a'); anchor.href = url; anchor.download = `campaigns-${selectedId}.csv`;
        document.body.appendChild(anchor); anchor.click(); anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } else {
        setRun(kind === 'start' ? await api.startRun(selectedId, startKeyForRun(selectedId)) : await api.cancelRun(selectedId));
        refresh();
      }
    } catch (error) { setActionError(error); }
    finally { pending.current = false; setAction(null); }
  }

  async function sendCommand(type: CommandType, parameters: Record<string, unknown>) {
    if (!selectedId || !team?.available_commands.includes(type) || pending.current) return;
    pending.current = true; setCommandError(null);
    try {
      const response = await api.command(selectedId, type, team.snapshot_id, parameters, crypto.randomUUID());
      commandRef.current = response; setCommand(response); refresh();
    } catch (error) { setCommandError(error); }
    finally { pending.current = false; }
  }

  return <Context.Provider value={{ selectedId, select, run, events, result, team, teamUnavailable,
    liveHandoff, loadError, actionError, action, refresh, perform, command, commandError, sendCommand }}>
    {children}
  </Context.Provider>;
}

export function useRun() {
  const state = useContext(Context);
  if (!state) throw new Error('RunProvider is required');
  return state;
}
