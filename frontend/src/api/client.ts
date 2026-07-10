import type {
  AuditRecord,
  DiagnosisEvent,
  DiagnosisSession,
  OpsEvent,
  OpsTask,
  Report,
  RunnerDiagnostics,
  SessionIdentity,
  Tool,
  ToolResult
} from "../types/api";

let csrfToken = "";

// Production uses the reverse proxy's same-origin /api route. Only the Vite
// development server resolves that route through VITE_DEV_API_PROXY_TARGET.
export function apiPath(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `/api${normalizedPath}`;
}

async function csrf(): Promise<string> {
  const response = await fetch(apiPath("/csrf"), { credentials: "include" });
  if (!response.ok) throw new Error("CSRF 初始化失败");
  const body = (await response.json()) as { csrf_token: string };
  csrfToken = body.csrf_token;
  return csrfToken;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers);
  if (method !== "GET") {
    if (!csrfToken) await csrf();
    headers.set("X-CSRF-Token", csrfToken);
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "include"
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return (await response.json()) as T;
}

export async function login(username: string, password: string) {
  await csrf();
  return request<{ identity: SessionIdentity; must_change_password: boolean }>(apiPath("/login"), {
    method: "POST",
    body: JSON.stringify({ username, password })
  });
}

export function changePassword(current_password: string, new_password: string) {
  return request<{ identity: SessionIdentity; must_change_password: boolean }>(apiPath("/me/password"), {
    method: "POST",
    body: JSON.stringify({ current_password, new_password })
  });
}

export function me() {
  return request<SessionIdentity>(apiPath("/me"));
}

export function getTools() {
  return request<Tool[]>(apiPath("/tools"));
}

export function getHealth() {
  return request<ToolResult>(apiPath("/health-snapshot"));
}

export function getRunnerDiagnostics() {
  return request<RunnerDiagnostics>(apiPath("/diagnostics/runner"));
}

export function createDiagnosis(question: string) {
  return request<DiagnosisSession>(apiPath("/diagnosis/sessions"), {
    method: "POST",
    body: JSON.stringify({ question })
  });
}

export async function getDiagnosisEvents(sessionId: string): Promise<DiagnosisEvent[]> {
  const response = await fetch(apiPath(`/diagnosis/sessions/${sessionId}/events`), {
    credentials: "include"
  });
  if (!response.ok) throw new Error(await response.text());
  const text = await response.text();
  return text
    .split("\n\n")
    .filter(Boolean)
    .map((chunk) => {
      const dataLine = chunk.split("\n").find((line) => line.startsWith("data: "));
      if (!dataLine) return null;
      return JSON.parse(dataLine.slice(6)) as DiagnosisEvent;
    })
    .filter((event): event is DiagnosisEvent => Boolean(event));
}

export function getTasks() {
  return request<OpsTask[]>(apiPath("/tasks"));
}

export function createTask(payload: {
  type?: string;
  title: string;
  resource?: string;
  risk_level?: string;
  reason?: string;
  simulate?: boolean;
}) {
  return request<OpsTask>(apiPath("/tasks"), {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function cancelTask(id: string) {
  return request<OpsTask>(apiPath(`/tasks/${id}/cancel`), { method: "POST" });
}

export async function getTaskEvents(taskId: string): Promise<OpsEvent[]> {
  const response = await fetch(apiPath(`/tasks/${taskId}/events`), {
    credentials: "include"
  });
  if (!response.ok) throw new Error(await response.text());
  const text = await response.text();
  return text
    .split("\n\n")
    .filter(Boolean)
    .map((chunk) => {
      const dataLine = chunk.split("\n").find((line) => line.startsWith("data: "));
      if (!dataLine) return null;
      return JSON.parse(dataLine.slice(6)) as OpsEvent;
    })
    .filter((event): event is OpsEvent => Boolean(event));
}

export function approve(id: string) {
  return request<{ status: string }>(apiPath(`/approvals/${id}/approve`), { method: "POST" });
}

export function deny(id: string) {
  return request<{ status: string }>(apiPath(`/approvals/${id}/deny`), { method: "POST" });
}

export function getAudit() {
  return request<AuditRecord[]>(apiPath("/audit"));
}

export function getReports() {
  return request<Report[]>(apiPath("/reports"));
}
