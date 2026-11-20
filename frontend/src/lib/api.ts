const ACCESS = "pf_access";
const REFRESH = "pf_refresh";

export type Tokens = { access_token: string; refresh_token: string };

export const tokens = {
  get: () => localStorage.getItem(ACCESS),
  set: (t: Tokens) => {
    localStorage.setItem(ACCESS, t.access_token);
    localStorage.setItem(REFRESH, t.refresh_token);
  },
  clear: () => {
    localStorage.removeItem(ACCESS);
    localStorage.removeItem(REFRESH);
  },
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// Refresh tokens rotate and re-use is treated as theft, so concurrent 401s must share ONE refresh call.
let refreshing: Promise<boolean> | null = null;

function tryRefresh(): Promise<boolean> {
  refreshing ??= (async () => {
    const refresh_token = localStorage.getItem(REFRESH);
    if (!refresh_token) return false;
    const r = await fetch("/api/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token }),
    });
    if (r.status === 401) {
      tokens.clear(); // token revoked/expired: the session is over
      return false;
    }
    if (!r.ok) throw new ApiError(r.status, "Server unavailable, please retry"); // keep tokens on 5xx/429
    tokens.set(await r.json());
    return true;
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

export async function logout(): Promise<void> {
  const refresh_token = localStorage.getItem(REFRESH);
  tokens.clear();
  if (refresh_token) {
    await fetch("/api/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token }),
    }).catch(() => {});
  }
}

/** POST that consumes a Server-Sent Events response, calling onEvent for each event. */
export async function apiStream(path: string, body: unknown, onEvent: (event: string, data: any) => void, retry = true): Promise<void> {
  const r = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${tokens.get() ?? ""}` },
    body: JSON.stringify(body),
  });
  if (r.status === 401 && retry && (await tryRefresh())) return apiStream(path, body, onEvent, false);
  if (!r.ok || !r.body) {
    let msg = r.statusText;
    try {
      msg = (await r.json()).detail ?? msg;
    } catch {}
    throw new ApiError(r.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      onEvent(event, data ? JSON.parse(data) : null);
    }
  }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}, retry = true): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const t = tokens.get();
  if (t) headers.Authorization = `Bearer ${t}`;
  const r = await fetch(`/api${path}`, {
    method: opts.method ?? (opts.body ? "POST" : "GET"),
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401 && retry && (await tryRefresh())) return api<T>(path, opts, false);
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new ApiError(r.status, msg);
  }
  return r.status === 204 ? (undefined as T) : r.json();
}

// ---- types ----
export type User = { id: string; email: string; full_name: string; role: string; tenant: { id: string; name: string; slug: string; plan: string } };
export type ProblemSummary = { id: string; title: string; difficulty: string; tags: string[]; is_global: boolean; solved: boolean; style?: "stdio" | "function" };
export type Signature = { function: string; params: { name: string; type: string }[]; returns: string; compare: "exact" | "unordered" };
export type TestCase = { id: string; input: string; expected_output: string; is_hidden: boolean };
export type Problem = ProblemSummary & { description: string; starter_code: Record<string, string>; signature?: Signature | null; contest?: { id: string; title: string; ends_at: string; locked: boolean } | null; sample_tests: TestCase[]; hidden_test_count: number };
export type TestResult = { index: number; status: string; passed: boolean; hidden: boolean; time_ms: number; input?: string; expected?: string; stdout?: string; stderr?: string; large?: boolean; printed?: string };
export type RunResult = { verdict: string; passed: number; total: number; runtime_ms: number; results: TestResult[] };
export type Submission = RunResult & {
  id: string; user_id: string; problem_id: string; language: string; code: string; ai_review: string | null; created_at: string;
  telemetry?: Record<string, number> | null; integrity_flags?: IntegrityFlag[] | null; // staff only
};
export type Message = {
  board_text?: string | null;
  seq: number; sender: "interviewer" | "candidate"; content: string; created_at: string;
  input_mode?: "text" | "voice"; code?: string | null; code_language?: string | null;
};
export type Delivery = {
  voice_answers: number; avg_words_per_answer: number; filler_words: number;
  fillers_per_100_words: number; top_fillers: string[]; notes: string[];
};
export type DocumentSummary = { id: string; kind: "resume" | "job_description"; title: string; chars: number; preview: string; created_at: string };
export type FitReport = {
  match_score: number; summary: string; matched_skills: string[]; missing_skills: string[];
  resume_suggestions: string[]; likely_questions: string[];
};
export type PlanItem = { problem_id: string; title: string; difficulty: string; reason: string; overdue_days?: number };
export type Plan = {
  streak_days: number; practiced_today: boolean; due_reviews: PlanItem[]; due_count: number; new_problems: PlanItem[];
  suggested_interview: { kind: string; focus: string; reason: string }; learned_count: number; next_review_at: string | null;
};

export type IntegrityFlag = { type: string; severity: "high" | "medium" | "low"; detail: string };
export type Comment = { id: string; author_id: string; author_name: string; author_role: string; body: string; created_at: string };
export type AssignmentProgress = {
  user_id: string; full_name: string; email: string; status: "completed" | "late" | "overdue" | "in_progress" | "not_started";
  done: number; total: number; solved_problem_ids: string[]; interviews_done: number; completed_at: string | null;
};
export type Assignment = {
  id: string; title: string; description: string; due_at: string; created_at: string;
  problems: { id: string; title: string; difficulty: string }[];
  interview_kind: string | null; interview_count: number; assign_all: boolean;
  assignee_count?: number; completed_count?: number; my_progress?: AssignmentProgress; progress?: AssignmentProgress[];
};

/** Download an authenticated file (e.g. CSV export) and save it with the server-provided name. */
export async function apiDownload(path: string, fallbackName: string): Promise<void> {
  const send = () => fetch(`/api${path}`, { headers: { Authorization: `Bearer ${tokens.get() ?? ""}` } });
  let r = await send();
  if (r.status === 401 && (await tryRefresh())) r = await send();
  if (!r.ok) throw new ApiError(r.status, r.statusText);
  const name = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") ?? "")?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Multipart upload (no JSON content-type, so the browser sets the boundary). */
export async function apiUpload<T = any>(path: string, form: FormData): Promise<T> {
  const send = () => fetch(`/api${path}`, { method: "POST", headers: { Authorization: `Bearer ${tokens.get() ?? ""}` }, body: form });
  let r = await send();
  if (r.status === 401 && (await tryRefresh())) r = await send();
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new ApiError(r.status, msg);
  }
  return r.json();
}
export type Scorecard = {
  overall_score: number; hire_signal: string; summary: string;
  dimensions: { name: string; score: number; comment: string }[];
  strengths: string[]; improvements: string[]; suggested_topics: string[];
  delivery?: Delivery;
};
export type Interview = {
  board?: { elements: any[] } | null;
  id: string; user_id: string; kind: string; role_title: string; level: string; focus: string; status: string;
  overall_score: number | null; scorecard: Scorecard | null; created_at: string; messages: Message[];
  has_resume?: boolean; has_job_description?: boolean; code?: string | null; code_language?: string | null;
};
