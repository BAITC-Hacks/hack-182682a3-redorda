import type { Channel, Run } from '../api/types';

export const statusLabels: Record<Run['status'], string> = {
  draft: 'Черновик', queued: 'В очереди', running: 'Выполняется',
  completed: 'Завершён', failed: 'Ошибка', cancelled: 'Отменён',
};
export const channelLabels: Record<Channel, string> = {
  push: 'Push', sms: 'SMS', digital_ads: 'Реклама', call: 'Звонок',
};
export const formatNumber = (value: number | string | null | undefined) => value == null ? 'Нет данных' : new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 2,
}).format(Number(value));
export const formatEffect = (value: string | null | undefined) => value == null ? 'Не рассчитан' : `${formatNumber(value)} у. е.`;
export const isActive = (run: Run) => run.status === 'queued' || run.status === 'running';

const warningLabels: Record<string, string> = {
  'history_unavailable: using broad priors and public catalogues': 'История переходов недоступна. Использованы текущие данные и общие начальные оценки.',
  'Pilot IDs unknown: membership is averaged analytically': 'Состав пилотных выборок неизвестен; пересечения аудиторий оценены приближённо.',
  'Tail reflects effect scenarios, not full membership risk': 'Нижние 10% модельных исходов отражают неопределённость эффекта, но не весь риск случайного состава пилотов.',
  'Unpiloted call uses a conservative saturation bound': 'Эффект звонков без пилота оценён консервативно.',
  'Campaign costs are incomplete; total spend is unknown.': 'Стоимость части кампаний неизвестна; общие расходы не рассчитаны.',
  'Campaign contact counts are incomplete; total contacts are unknown.': 'Для части кампаний неизвестен охват; общее число контактов не рассчитано.',
  'Explanations are missing for some campaigns.': 'Для части кампаний не сохранены объяснения.',
  'Predicted effect was not calculated.': 'Прогноз эффекта не рассчитан.',
  'Simulator result was not calculated.': 'Результат симулятора не рассчитан.',
};
export const warningText = (warning: string) => warning.startsWith('hypothesis_fallback:')
  ? 'Гипотезы OpenAI недоступны; использован расчётный поиск.' : warningLabels[warning] || warning;
