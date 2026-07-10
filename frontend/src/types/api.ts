export interface SessionIdentity {
  actor_id: string;
  session_id: string;
  role: "admin";
  created_at: string;
  expires_at: string;
  must_change_password: boolean;
}

export interface Tool {
  name: string;
  category: string;
  description: string;
  risk_level: "read" | "mutating" | "destructive";
  approval_required: boolean;
  timeout_seconds: number;
  enabled: boolean;
  disabled_reason_code?: string | null;
}

export interface ToolResult {
  tool_name: string;
  status: string;
  started_at?: string;
  duration_ms?: number;
  output_summary: string;
  output_ref?: string;
  redacted?: boolean;
  error?: string | null;
  data: Record<string, unknown>;
}

export interface DiagnosisSession {
  id: string;
  status: string;
  user_question: string;
  created_at: string;
  updated_at: string;
}

export interface DiagnosisEvent {
  type: string;
  session_id: string;
  sequence: number;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface OpsEvent {
  id: string;
  scope: string;
  scope_id: string;
  sequence: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface OpsSubtask {
  id: string;
  task_id: string;
  title: string;
  status: string;
  sequence: number;
  started_at?: string | null;
  finished_at?: string | null;
  rollback_step: boolean;
  output_ref?: string | null;
  failure_reason?: string | null;
}

export interface OpsTask {
  id: string;
  type: string;
  title: string;
  status: string;
  actor_id: string;
  resource: string;
  risk_level: string;
  reason: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  cancel_requested: boolean;
  failure_reason?: string | null;
  approval_id?: string | null;
  subtasks?: OpsSubtask[];
  events?: OpsEvent[];
}

export interface AuditRecord {
  id: string;
  actor_id: string;
  event_type: string;
  resource: string;
  risk_level: string;
  status: string;
  summary: string;
  created_at: string;
}

export interface Report {
  id: string;
  type: string;
  health_score: number;
  risk_items: Array<Record<string, unknown>>;
  evidence_refs: string[];
  recommended_actions: Array<string | Record<string, unknown>>;
  created_at: string;
}

export interface RunnerDiagnostics {
  runner_user: string;
  effective_uid: number;
  bind_host: string;
  privileged_adapters_enabled: string[];
  sudo_enabled: boolean;
  tool_count: number;
  llm_mode: string;
  agent_id: string;
  agent_transport: string;
  agent_status: string;
  command_runner: string;
  streaming_enabled: boolean;
  cancel_supported: boolean;
  redaction_enabled: boolean;
  shell_enabled: boolean;
}
