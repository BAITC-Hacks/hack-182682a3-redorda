import type { Channel, Run } from '../api/types';

export const statusLabels: Record<Run['status'], string> = {
  draft: 'Черновик', queued: 'В очереди', running: 'Выполняется',
  completed: 'Завершён', failed: 'Ошибка', cancelled: 'Отменён',
};
export const channelLabels: Record<Channel, string> = {
  push: 'Push', sms: 'SMS', digital_ads: 'Реклама', call: 'Звонок',
};
export const formatNumber = (value: number | string) => new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 2,
}).format(Number(value));
export const formatEffect = (value: string | null | undefined) => value == null ? 'Не рассчитан' : `${formatNumber(value)} у. е.`;
export const isActive = (run: Run) => run.status === 'queued' || run.status === 'running';
