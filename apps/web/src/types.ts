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
  image_uri?: string | null;
  /** Arrives asynchronously after the verdict. null = model has not answered yet. */
  narrative?: string | null;
  narrative_provenance?: NarrativeProvenance | null;
  assigned_to_username?: string | null;
  assigned_to_name?: string | null;
  assigned_by_name?: string | null;
  assigned_at?: string | null;
}

export interface NarrativeProvenance {
  summary: string;
  prompt_version: string;
  degradations: string[];
  latency_ms: number;
  source: string;
  model: string | null;
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

export interface DefectDetail {
  defect_type: string;
  severity: string;
  location: string;
  description: string;
  confidence: number;
}

export interface VisionProvenance {
  source: string;
  producer: string;
  tier: string | null;
  model_id: string | null;
  latency_ms: number | null;
  degradations: string[];
  prompt_version: string | null;
}

export interface VisionAnalysis {
  defects_found: boolean;
  defect_count: number;
  defects: DefectDetail[];
  component_identified: string;
  overall_condition: string;
  confidence: number;
  recommendations: string[];
  provenance: VisionProvenance;
  image_dimensions: string;
}
