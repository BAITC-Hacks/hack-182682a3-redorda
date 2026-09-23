import type { RunEvent, TeamTask } from '../../api/types';

export const PETS = [
  { name: 'Жан', role: 'Контролёр', actorIds: ['control'], color: '#9d87ed', light: '#d5c8ff', dark: '#7661bd', kind: 'courier' },
  { name: 'Дана', role: 'Аналитик / финансист', actorIds: ['analyst', 'finance'], color: '#76a9f7', light: '#c2dcff', dark: '#5280cc', kind: 'analyst' },
  { name: 'Айя', role: 'Экспериментатор', actorIds: ['experiment'], color: '#ef9ab8', light: '#ffd5df', dark: '#cd7897', kind: 'caller' },
  { name: 'Бек', role: 'Руководитель', actorIds: ['lead'], color: '#f4c94f', light: '#fff0aa', dark: '#c59b31', kind: 'captain' },
] as const;

export const DESKS = [
  { x: .17, y: .32 }, { x: .68, y: .32 }, { x: .17, y: .82 }, { x: .68, y: .82 },
] as const;

export function petIndexForActor(actor: unknown): number {
  return PETS.findIndex(pet => pet.actorIds.some(id => id === actor));
}

export function latestTaskForPet(tasks: TeamTask[], events: RunEvent[], index: number): TeamTask | null {
  const matches = tasks.filter(task => petIndexForActor(task.actor_id) === index);
  if (!matches.length) return null;
  const current = matches.filter(task => task.status === 'running').at(-1);
  if (current) return current;
  const eventOrder = new Map<string, number>();
  events.forEach(event => {
    if (typeof event.payload.task_id === 'string') eventOrder.set(event.payload.task_id, event.id);
  });
  return matches.sort((a, b) => (eventOrder.get(b.id) ?? 0) - (eventOrder.get(a.id) ?? 0))[0];
}

export function roleForPet(index: number, task: TeamTask | null): string {
  if (index === 1) return task?.actor_id === 'finance' ? 'Финансист' : 'Аналитик';
  return PETS[index].role;
}

export function handoffActors(event: RunEvent | null): { from: number; to: number; taskId: string | null } | null {
  if (!event || event.event_kind !== 'task_handoff') return null;
  const from = petIndexForActor(event.payload.from_actor);
  const to = petIndexForActor(event.payload.to_actor);
  if (from < 0 || to < 0) return null;
  return { from, to, taskId: typeof event.payload.to_task_id === 'string' ? event.payload.to_task_id : null };
}
