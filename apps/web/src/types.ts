export interface Fastener {
  position: number;
  final_torque_nm: number;
  knee_angle_deg: number;
  elastic_slope_nm_per_deg: number;
  anomaly_score: number;
  endpoint_in_spec: boolean;
  signature_anomalous: boolean;
  fusion_only: boolean;
  likely_class: string | null;
  deviations: string[];
  curve: number[][];
}

export interface Span {
  agent: string;
  duration_ms: number;
  summary: string;
  ok: boolean;
}

export interface Baseline {
  knee_angle_deg: number;
  knee_tolerance_deg: number;
  elastic_slope_nm_per_deg: number;
  elastic_slope_tolerance: number;
  spec_lo_nm: number;
  spec_hi_nm: number;
  derived_from_runs: number;
  sigma: number;
}

export interface Inspection {
  correlation_id: string;
  unit_id: string;
  pack_id: string;
  verdict: "pass" | "defect" | "escalate";
  severity: string;
  confidence: number;
  fusion_only: boolean;
  primary_signal: string;
  reasoning: string;
  disposition: string;
  expected_cost: number;
  cost_low: number;
  cost_high: number;
  currency: string;
  cost_assumptions: string[];
  requires_human: boolean;
  human_reason: string;
  data_quality: string;
  data_quality_reasons: string[];
  fasteners: Fastener[];
  spans: Span[];
  total_ms: number;
  is_synthetic: boolean;
  created_at: string;
  baseline: Baseline;
}

export interface Scenario {
  id: string;
  label: string;
  note: string;
}

export interface Metrics {
  inspected: number;
  defects?: number;
  fusion_only?: number;
  escalated?: number;
  first_pass_yield?: number;
  p50_ms?: number;
  p95_ms?: number;
  cost_at_risk?: number;
  currency?: string;
}
