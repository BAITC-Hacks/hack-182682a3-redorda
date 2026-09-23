import type { CommandType } from './types';

export interface RequestIntent {
  type: CommandType;
  campaignId?: string;
  budget?: string;
  planName?: string;
}

// Free text only prepares a typed command; the server remains the authority for execution.
export function parseTeamRequest(input: string): RequestIntent | null {
  const text = input.trim();
  const explain = /^объясни\s+(.+)$/i.exec(text);
  if (explain) return { type: 'explain', campaignId: explain[1].trim() };
  const compare = /^сравни(?:\s+бюджет\s+(\d+(?:[.,]\d{1,2})?))?$/i.exec(text);
  if (compare) return { type: 'compare', ...(compare[1] ? { budget: compare[1].replace(',', '.') } : {}) };
  const create = /^создай\s+план\s+(.+)$/i.exec(text);
  if (create) {
    const name = create[1].trim();
    const constrained = /^(?:(.*?)\s+)?с\s+бюджетом\s+(\d+(?:[.,]\d{1,2})?)$/i.exec(name);
    if (constrained) {
      const budget = constrained[2].replace(',', '.');
      return { type: 'create_plan', planName: constrained[1]?.trim() || `План с бюджетом ${budget}`, budget };
    }
    if (/(?:^|\s)с\s+бюджетом(?:\s|$)/i.test(name)) return null;
    return { type: 'create_plan', planName: name };
  }
  return null;
}
