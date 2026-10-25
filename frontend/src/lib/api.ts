const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api/v1";
let csrfToken = "";

export function setCsrfToken(token: string) {
  csrfToken = token;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  });

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { error?: { message?: string } };
      message = body.error?.message || message;
    } catch {
      // Keep the status-based message when the response has no JSON body.
    }
    throw new ApiError(message, response.status);
  }

  return (await response.json()) as T;
}

async function apiWrite<T>(path: string, method: "POST" | "PATCH" | "DELETE", body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    credentials: "same-origin",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const result = (await response.json()) as { error?: { message?: string } };
      message = result.error?.message || message;
    } catch {
      // Preserve the status-based message when there is no JSON response body.
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function apiPost<T>(path: string, body?: unknown) {
  return apiWrite<T>(path, "POST", body);
}

export function apiPatch<T>(path: string, body: unknown) {
  return apiWrite<T>(path, "PATCH", body);
}

export function apiDelete<T = void>(path: string) {
  return apiWrite<T>(path, "DELETE");
}

export type Workspace = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  created_at: string;
  role?: WorkspaceMembership["role"];
};

export type Environment = {
  id: string;
  workspace_id: string;
  slug: string;
  name: string;
  environment_type: string;
  cluster_name: string | null;
  namespace: string | null;
  protected: boolean;
  allow_destroy: boolean;
  quota_cpu_millis: number | null;
  quota_memory_mib: number | null;
  quota_pods: number | null;
  quota_persistent_volume_claims: number | null;
  quota_services: number | null;
  created_at: string;
};

export type SystemStatus = {
  api: "healthy" | "unavailable" | string;
  database: "healthy" | "unavailable" | string;
  worker: "healthy" | "starting" | "stale" | "unavailable" | string;
  worker_last_seen_at: string | null;
  checked_at: string;
};

export type User = {
  id: string;
  email: string;
  display_name: string;
  email_verified: boolean;
  is_platform_admin: boolean;
  active: boolean;
  created_at: string;
};

export type WorkspaceMembership = {
  workspace_id: string;
  role: "workspace_admin" | "platform_engineer" | "service_owner" | "auditor" | "platform_admin";
};

export type AuthSession = {
  user: User;
  memberships: WorkspaceMembership[];
  csrf_token: string;
};

export type AuthConfig = {
  oidc_enabled: boolean;
  development_login_enabled: boolean;
  authentication_required: boolean;
};

export type WorkspaceMember = {
  membership_id: string;
  user: User;
  role: "workspace_admin" | "platform_engineer" | "service_owner" | "auditor";
  created_at: string;
};

export type AuditEvent = {
  id: string;
  workspace_id: string | null;
  actor_type: string;
  actor_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  outcome: "succeeded" | "failed" | "denied" | string;
  details: Record<string, unknown>;
  created_at: string;
};

export type ServiceLifecycle = "experimental" | "production" | "deprecated" | "retired";
export type ServiceCriticality = "tier_0" | "tier_1" | "tier_2" | "tier_3";

export type ServiceRecord = {
  id: string;
  workspace_id: string;
  slug: string;
  name: string;
  description: string | null;
  system: string | null;
  owner_team_id: string | null;
  owner_team_name: string | null;
  repository_url: string | null;
  documentation_url: string | null;
  lifecycle: ServiceLifecycle;
  criticality: ServiceCriticality;
  tags: string[];
  metadata: Record<string, unknown>;
  golden_path_id: string | null;
  golden_path_slug: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
};

export type ServiceTeam = {
  id: string;
  workspace_id: string;
  slug: string;
  name: string;
  description: string | null;
  service_count: number;
  created_at: string;
  updated_at: string;
};

export type GoldenPath = {
  id: string;
  slug: string;
  name: string;
  description: string;
  category: string;
  icon: string;
  version: string;
  status: string;
  capabilities: string[];
  config_schema: Array<{
    key: string;
    label: string;
    type: "select" | "text";
    options?: string[];
  }>;
  defaults: Record<string, unknown>;
};

export type ServiceCatalog = {
  items: ServiceRecord[];
  total: number;
  limit: number;
  offset: number;
};

export type ServiceCatalogSummary = {
  total: number;
  production: number;
  experimental: number;
  ownership_teams: number;
};

export type Deployment = {
  id: string;
  workspace_id: string;
  service_id: string;
  environment_id: string;
  version: string;
  source_ref: string | null;
  status: string;
  requested_by: string | null;
  idempotency_key: string | null;
  attempt_count: number;
  max_attempts: number;
  started_at: string | null;
  finished_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  failure_code: string | null;
  failure_message: string | null;
  created_at: string;
  updated_at: string;
};

export type DeploymentEvent = {
  id: string;
  deployment_id: string;
  workspace_id: string;
  sequence: number;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  actor_type: string;
  actor_id: string | null;
  message: string;
  details: Record<string, unknown>;
  created_at: string;
};

export type BuildRun = {
  id: string;
  workspace_id: string;
  deployment_id: string;
  service_id: string;
  attempt_number: number;
  provider: string;
  repository_url: string | null;
  workflow_path: string;
  source_ref: string;
  commit_sha: string | null;
  gitops_revision: string | null;
  status: string;
  attempt_count: number;
  max_attempts: number;
  external_run_id: string | null;
  run_url: string | null;
  callback_deadline_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  failure_code: string | null;
  failure_message: string | null;
  created_at: string;
  updated_at: string;
};

export type GitOpsTarget = {
  id: string;
  workspace_id: string;
  service_id: string;
  environment_id: string;
  application_name: string;
  expected_project: string;
  expected_cluster_name: string | null;
  artifact_name: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type GitOpsRun = {
  id: string;
  workspace_id: string;
  deployment_id: string;
  build_run_id: string | null;
  target_id: string | null;
  rollback_from_run_id: string | null;
  attempt_number: number;
  action_kind: string;
  application_name: string;
  desired_revision: string | null;
  observed_revision: string | null;
  artifact_uri: string | null;
  artifact_digest: string | null;
  status: string;
  sync_status: string | null;
  health_status: string | null;
  operation_phase: string | null;
  destination_name: string | null;
  destination_server: string | null;
  attempt_count: number;
  max_attempts: number;
  callback_deadline_at: string | null;
  failure_code: string | null;
  failure_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ArgoApplicationStatus = {
  application_name: string;
  provider_status: string;
  failure_code: string | null;
  project: string | null;
  sync_status: string | null;
  health_status: string | null;
  operation_phase: string | null;
  revision: string | null;
  destination_name: string | null;
  destination_server: string | null;
};

export type InfrastructureTarget = {
  id: string;
  workspace_id: string;
  environment_id: string;
  repository_url: string;
  source_ref: string;
  working_directory: string;
  tool: "terraform" | "opentofu";
  backend_workspace: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type InfrastructureRun = {
  id: string;
  workspace_id: string;
  environment_id: string;
  target_id: string | null;
  requested_by: string | null;
  approved_by: string | null;
  attempt_number: number;
  attempt_count: number;
  max_attempts: number;
  run_kind: string;
  active_operation: string | null;
  status: string;
  source_ref: string;
  working_directory: string;
  external_run_id: string | null;
  run_url: string | null;
  plan_digest: string | null;
  plan_artifact_uri: string | null;
  plan_has_changes: boolean | null;
  resource_additions: number | null;
  resource_changes: number | null;
  resource_destroys: number | null;
  quota_usage: Record<string, number>;
  callback_deadline_at: string | null;
  failure_code: string | null;
  failure_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
};

export type PolicyRule = {
  id: string;
  name: string;
  description?: string | null;
  field: string;
  operator: string;
  expected?: unknown;
  effect: string;
};

export type Policy = {
  id: string;
  workspace_id: string;
  slug: string;
  name: string;
  description: string | null;
  enabled: boolean;
  enforcement_mode: string;
  actions: string[];
  rules: PolicyRule[];
  version: number;
  created_by: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
};

export type PolicyEvaluation = {
  id: string;
  workspace_id: string;
  action: string;
  subject_type: string;
  subject_id: string;
  decision: string;
  requires_approval: boolean;
  context: Record<string, unknown>;
  policy_snapshot: Array<Record<string, unknown>>;
  policy_snapshot_sha256: string;
  results: Array<Record<string, unknown>>;
  created_at: string;
};

export type PolicyException = {
  id: string;
  workspace_id: string;
  policy_id: string;
  policy_version: number;
  rule_id: string;
  service_id: string | null;
  environment_id: string | null;
  reason: string;
  status: string;
  requested_by: string | null;
  reviewed_by: string | null;
  review_comment: string | null;
  expires_at: string;
  reviewed_at: string | null;
  created_at: string;
};

export type TelemetrySource = {
  id: string;
  workspace_id: string;
  service_id: string;
  environment_id: string | null;
  name: string;
  token_prefix: string;
  enabled: boolean;
  created_by: string | null;
  created_at: string;
  last_seen_at: string | null;
};

export type Slo = {
  id: string;
  workspace_id: string;
  service_id: string;
  environment_id: string | null;
  slug: string;
  name: string;
  indicator: "availability" | "latency";
  objective: number;
  window_hours: number;
  latency_threshold_ms: number | null;
  enabled: boolean;
  created_by: string | null;
  created_at: string;
  last_evaluated_at: string | null;
};

export type SloEvaluation = {
  id: string;
  workspace_id: string;
  slo_id: string;
  status: string;
  window_start: string;
  window_end: string;
  total_events: number;
  good_events: number;
  attainment: number | null;
  error_budget_remaining: number | null;
  burn_rate: number | null;
  created_at: string;
};

export type SloAlert = {
  id: string;
  workspace_id: string;
  slo_id: string;
  evaluation_id: string;
  status: "firing" | "acknowledged" | "resolved" | string;
  summary: string;
  details: Record<string, unknown>;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  resolved_at: string | null;
  created_at: string;
};

