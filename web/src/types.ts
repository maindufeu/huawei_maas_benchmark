export interface ClassInfo { id: string; label: string; }
export interface TaskInfo {
  id: string; task_class: string; title: string;
  language: string; eval_type: string; difficulty: string;
}
export interface HuaweiModel { model: string; tier: string; }
export interface Config {
  classes: ClassInfo[];
  tasks: TaskInfo[];
  huawei_models: HuaweiModel[];
  p95_gate: number;
}

export interface ProviderPublic {
  index: number; name: string; base_url: string; model: string;
  tier: string; is_local: boolean; has_key: boolean;
  extra_body?: Record<string, unknown>;
}

export interface TestResult {
  name: string; model: string; ok: boolean; status: string;
  latency_s: number; error_type: string | null; error_message: string;
}

export interface RunState {
  run_id: string | null; status: string;
  done: number; total: number; error: string | null;
}

export interface ProgressEvent {
  type: string; done?: number; total?: number;
  task_id?: string; task_class?: string; model?: string;
  status?: string; passed?: boolean; latency_s?: number;
  eval_score?: number; error_type?: string | null;
  message?: string; count?: number;
}

export interface Summary {
  calls: number; pass_at_1: number; filter_rate: number;
  error_rate: number; p95_gate: number;
}

export type Row = Record<string, any>;

export interface PriceRate {
  input?: number; output?: number; cached_input?: number; note?: string;
}
export type PricingTable = Record<string, PriceRate>;

export interface Results {
  summary: Summary | Record<string, never>;
  raw: Row[];
  aggregate: Row[];
  composite: Row[];
  decision: Row[];
}

export interface RunMeta {
  run_id: string; status: string; saved_at: string;
  file: string; calls: number; pass_at_1: number | null;
}

export interface RunConfig {
  task_classes: string[];
  only_objective: boolean;
  repetitions: number;
  temperature: number;
  max_tokens: number;
  stream: boolean;
  warmup: boolean;
  timeout_s: number;
  /** endpoints hit concurrently: 0 = all in parallel, 1 = sequential */
  max_parallel: number;
  /** task ids that get extra repetitions */
  focus_tasks: string[];
  focus_extra_reps: number;
}
