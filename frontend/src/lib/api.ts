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

async function tryRefresh(): Promise<boolean> {
  const refresh_token = localStorage.getItem(REFRESH);
  if (!refresh_token) return false;
  const r = await fetch("/api/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token }),
  });
  if (!r.ok) return false;
  tokens.set(await r.json());
  return true;
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
export type ProblemSummary = { id: string; title: string; difficulty: string; tags: string[]; is_global: boolean; solved: boolean };
export type TestCase = { id: string; input: string; expected_output: string; is_hidden: boolean };
export type Problem = ProblemSummary & { description: string; starter_code: Record<string, string>; sample_tests: TestCase[]; hidden_test_count: number };
export type TestResult = { index: number; status: string; passed: boolean; hidden: boolean; time_ms: number; input?: string; expected?: string; stdout?: string; stderr?: string };
export type RunResult = { verdict: string; passed: number; total: number; runtime_ms: number; results: TestResult[] };
export type Submission = RunResult & { id: string; problem_id: string; language: string; code: string; ai_review: string | null; created_at: string };
export type Message = { seq: number; sender: "interviewer" | "candidate"; content: string; created_at: string };
export type Scorecard = {
  overall_score: number; hire_signal: string; summary: string;
  dimensions: { name: string; score: number; comment: string }[];
  strengths: string[]; improvements: string[]; suggested_topics: string[];
};
export type Interview = {
  id: string; kind: string; role_title: string; level: string; focus: string; status: string;
  overall_score: number | null; scorecard: Scorecard | null; created_at: string; messages: Message[];
};
