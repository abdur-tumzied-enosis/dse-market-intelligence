const BASE =
  typeof window === "undefined"
    ? `${process.env.MGMT_API_URL ?? "http://localhost:8001"}/mgmt`
    : "/api/mgmt";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

async function put<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

async function patch<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// ── Types ────────────────────────────────────────────────────────────

export interface AdapterInfo {
  name: string;
  base_priority: number;
  effective_priority: number;
  priority_delta: number;
  paused: boolean;
  timeout_seconds: number;
}

export interface StreamInfo {
  name: string;
  adapter_count: number;
  active_adapters: number;
  adapters: AdapterInfo[];
}

export interface FreshnessEntry {
  stream: string;
  last_success_at: string | null;
  adapter_used: string | null;
  records_inserted: number | null;
  stale: boolean;
}

export interface SourceHealth {
  source_name: string;
  url: string;
  reachable: boolean;
  status_code: number | null;
  response_ms: number | null;
  checked_at: string;
  hash_changed: boolean;
}

export interface AlertSummary {
  critical_unacked: number;
  warning_unacked: number;
  total_unacked: number;
  last_24h: number;
}

export interface Alert {
  id: number;
  severity: string;
  stream_name: string | null;
  message: string;
  details: Record<string, unknown>;
  created_at: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
}

export interface TaskInfo {
  name: string;
  description: string;
  queue: string;
  task_name: string;
  estimated_duration: string;
}

export interface TaskRun {
  task_id: string;
  status: string;
  task_name: string;
  queue: string;
}

export interface TaskStatus {
  task_id: string;
  status: string;
  result: unknown | null;
  traceback: string | null;
}

export interface Decision {
  id: number;
  decided_at: string;
  run_mode: string;
  action_type: string;
  target: string;
  reasoning: string;
  risk_level: string;
  status: string;
  approved_by: string | null;
  approved_at: string | null;
  outcome: string | null;
  tool_calls?: string;
}

export interface SchedulerJob {
  id: string;
  name: string;
  next_run: string | null;
  trigger: string;
  paused: boolean;
}

export interface TierLimit {
  tier: string;
  limit_key: string;
  limit_value: number;
  updated_at: string | null;
}

export interface FeatureFlag {
  flag_key: string;
  tier: string;
  enabled: boolean;
  updated_at: string | null;
}

export interface UserSummary {
  id: number;
  email: string;
  tier: string;
  is_active: boolean;
}

export interface UserOverride {
  id: number;
  user_id: number;
  flag_key: string;
  override: "grant" | "revoke";
  expires_at: string | null;
  note: string | null;
  created_at: string | null;
}

// ── API calls ────────────────────────────────────────────────────────

export const api = {
  streams: {
    list: () => get<StreamInfo[]>("/streams"),
    get: (name: string) => get<StreamInfo>(`/streams/${name}`),
    pauseAdapter: (stream: string, adapter: string) =>
      post(`/streams/${stream}/adapters/${adapter}/pause`),
    resumeAdapter: (stream: string, adapter: string) =>
      post(`/streams/${stream}/adapters/${adapter}/resume`),
    promoteAdapter: (stream: string, adapter: string) =>
      post(`/streams/${stream}/adapters/${adapter}/promote`),
    demoteAdapter: (stream: string, adapter: string) =>
      post(`/streams/${stream}/adapters/${adapter}/demote`),
  },
  quality: {
    freshness: () => get<FreshnessEntry[]>("/quality/freshness"),
    failures: () => get<unknown[]>("/quality/failures"),
  },
  health: {
    summary: () => get<SourceHealth[]>("/health"),
    hashes: () => get<unknown[]>("/health/structure-hashes"),
  },
  alerts: {
    list: (params?: { unacked_only?: boolean; severity?: string }) => {
      const q = new URLSearchParams();
      if (params?.unacked_only) q.set("unacked_only", "true");
      if (params?.severity) q.set("severity", params.severity);
      return get<Alert[]>(`/alerts?${q}`);
    },
    summary: () => get<AlertSummary>("/alerts/summary"),
    acknowledge: (id: number) => post(`/alerts/${id}/acknowledge`, { acknowledged_by: "ui" }),
  },
  scheduler: {
    list: () => get<SchedulerJob[]>("/scheduler"),
    trigger: (id: string) => post(`/scheduler/${id}/trigger`),
    pause: (id: string) => post(`/scheduler/${id}/pause`),
    resume: (id: string) => post(`/scheduler/${id}/resume`),
  },
  tasks: {
    list: () => get<TaskInfo[]>("/tasks"),
    run: (name: string) => post<TaskRun>(`/tasks/${name}/run`),
    status: (taskId: string) => get<TaskStatus>(`/tasks/results/${taskId}`),
  },
  agent: {
    status: () => get<unknown>("/agent/status"),
    run: () => post<unknown>("/agent/run"),
    decisions: (params?: { status?: string }) =>
      get<Decision[]>(`/agent/decisions${params?.status ? `?status=${params.status}` : ""}`),
    queue: () => get<Decision[]>("/agent/queue"),
    approve: (id: number) => post(`/agent/decisions/${id}/approve`),
    reject: (id: number) => post(`/agent/decisions/${id}/reject`),
  },
  access: {
    tierLimits: () => get<TierLimit[]>("/access/tier-limits"),
    updateTierLimit: (tier: string, key: string, value: number) =>
      put(`/access/tier-limits/${tier}/${key}`, { limit_value: value }),
    features: () => get<FeatureFlag[]>("/access/features"),
    updateFeature: (flagKey: string, tier: string, enabled: boolean) =>
      put(`/access/features/${flagKey}/${tier}`, { enabled }),
    listUsers: (limit = 500) =>
      get<UserSummary[]>(`/access/users?limit=${limit}`),
    updateUser: (userId: number, fields: { tier?: string; is_active?: boolean }) =>
      patch<UserSummary>(`/access/users/${userId}`, fields),
    searchUsers: (email: string) =>
      get<UserSummary[]>(`/access/users?email=${encodeURIComponent(email)}`),
    userOverrides: (userId: number) =>
      get<UserOverride[]>(`/access/users/${userId}/overrides`),
    createOverride: (
      userId: number,
      body: { flag_key: string; override: string; expires_at?: string; note?: string }
    ) => post<UserOverride>(`/access/users/${userId}/overrides`, body),
    deleteOverride: (userId: number, flagKey: string) =>
      del<{ deleted: boolean }>(`/access/users/${userId}/overrides/${flagKey}`),
  },
};

// SSE chat helper
export function chatStream(
  messages: Array<{ role: string; content: string }>,
  onChunk: (chunk: { type: string; text?: string; name?: string; input?: unknown; result?: unknown }) => void,
  onDone: () => void,
  onError: (err: Error) => void,
): () => void {
  const ctrl = new AbortController();

  fetch(`${BASE}/agent/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
    signal: ctrl.signal,
  })
    .then(async (res) => {
      if (!res.ok || !res.body) throw new Error(`${res.status}`);
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.replace(/^data: /, "").trim();
          if (!line) continue;
          try {
            const chunk = JSON.parse(line);
            if (chunk.type === "done") onDone();
            else onChunk(chunk);
          } catch {}
        }
      }
    })
    .catch((err) => {
      if (err.name !== "AbortError") onError(err);
    });

  return () => ctrl.abort();
}
