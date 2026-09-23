export interface Dataset {
  id: string;
  name: string;
  checksum: string;
  customer_count: number;
  imported_at: string;
  summary: {
    baseline_arpu: string | number | null;
    tariff_count: number;
    synthetic: boolean | null;
    source_kind?: 'demo' | 'upload';
    format?: 'raw_csv';
    file_rows?: Record<string, number>;
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
