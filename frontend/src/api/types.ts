export interface Dataset {
  id: string;
  name: string;
  checksum: string;
  customer_count: number;
  imported_at: string;
  summary: {
    baseline_arpu: string;
    tariff_count: number;
    synthetic: boolean;
    segments: Record<'arpu_segment' | 'data_segment' | 'call_segment', Record<string, number>>;
  };
}

export interface Run {
  id: string;
  name: string;
  dataset_id: string;
  status: 'draft' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  budget: string;
  max_contacts: number;
  max_pilots: number;
  seed: number;
  strategy: 'baseline' | 'openai';
  created_at: string;
  progress?: RunProgress | null;
  error?: { code: string; message: string } | null;
  cancellation_requested?: boolean;
}

export interface RunProgress {
  stage: string;
  percent: number | null;
  spent: string;
  contacts_used: number;
  pilots_completed: number;
}

export type Channel = 'push' | 'sms' | 'digital_ads' | 'call';

export interface RunEvent {
  id: number;
  created_at: string;
  kind: 'info' | 'pilot' | 'warning' | 'error';
  message: string;
  pilot?: {
    campaign_name: string;
    channel: Channel;
    target_tariff: string;
    customers: number;
    cost: string;
    observed_effect: string | null;
  } | null;
}

export interface CampaignResult {
  id: string;
  campaign_name: string;
  target_tariff: string;
  channel: Channel;
  audience: string;
  customers: number;
  cost: string;
  forecast_effect: string | null;
  rationale: string;
}

export interface RunResults {
  campaigns: CampaignResult[];
  totals: {
    spent: string;
    contacts_used: number;
    pilots_completed: number;
    forecast_effect: string | null;
    simulated_effect: string | null;
  };
}

export interface RunInput {
  name: string;
  budget: string;
  max_contacts: number;
  max_pilots: number;
  seed: number;
  strategy: 'baseline';
}

export interface Page<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface Meta {
  limits: Record<string, number>;
  channel_costs: Record<string, number>;
  features: { run_execution: boolean; openai_strategy: boolean; csv_export: boolean };
}
