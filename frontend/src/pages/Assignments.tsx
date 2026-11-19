import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, apiDownload, type Assignment, type AssignmentProgress, type ProblemSummary } from "../lib/api";
import { isStaff, useAuth } from "../lib/auth";

const STATUS_LABEL: Record<AssignmentProgress["status"], string> = {
  completed: "Completed", late: "Completed late", overdue: "Overdue", in_progress: "In progress", not_started: "Not started",
};

function StatusPill({ status }: { status: AssignmentProgress["status"] }) {
  return <span className={`pill status-${status}`}>{STATUS_LABEL[status]}</span>;
}

function dueLabel(iso: string) {
  const d = new Date(iso);
  const days = Math.ceil((d.getTime() - Date.now()) / 86_400_000);
  const when = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return days < 0 ? `was due ${when}` : days === 0 ? "due today" : `due ${when} (${days}d)`;
}

export default function Assignments() {
  const { user } = useAuth();
  const staff = isStaff(user);
  const [items, setItems] = useState<Assignment[] | null>(null); // null = still loading
  const [loadError, setLoadError] = useState("");
  const [creating, setCreating] = useState(false);
  const load = () => api<Assignment[]>("/assignments").then(setItems).catch((e) => setLoadError(e.message));
  useEffect(() => {
    load();
  }, []);

  return (
    <div className="stack">
      <div className="row between">
        <h2>Assignments</h2>
        {staff && <button className="primary" onClick={() => setCreating(true)}>+ New assignment</button>}
      </div>
      {creating && <AssignmentForm onDone={() => { setCreating(false); load(); }} />}
      {loadError && <div className="error">Couldn't load assignments: {loadError}</div>}
      {items === null && !loadError && <p className="muted">Loading…</p>}
      {items?.length === 0 && <p className="muted">{staff ? "No assignments yet. Create one to give your cohort practice with a deadline." : "You have no assignments right now."}</p>}
      {items?.map((a) => (
        <Link key={a.id} to={`/assignments/${a.id}`} className="card assignment-card">
          <div className="row between">
            <b>{a.title}</b>
            {a.my_progress ? <StatusPill status={a.my_progress.status} /> : (
              <span className="small muted">{a.completed_count}/{a.assignee_count} completed</span>
            )}
          </div>
          <div className="small muted">
            {dueLabel(a.due_at)} · {a.problems.length} problem{a.problems.length === 1 ? "" : "s"}
            {a.interview_count > 0 && ` · ${a.interview_count} ${a.interview_kind ?? ""} interview${a.interview_count > 1 ? "s" : ""}`}
          </div>
          {a.my_progress && <Progress done={a.my_progress.done} total={a.my_progress.total} />}
          {a.assignee_count !== undefined && <Progress done={a.completed_count ?? 0} total={a.assignee_count} />}
        </Link>
      ))}
    </div>
  );
}

function Progress({ done, total }: { done: number; total: number }) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  return <div className="progress" title={`${done}/${total}`}><div style={{ width: `${pct}%` }} /></div>;
}

function AssignmentForm({ onDone }: { onDone: () => void }) {
  const [problems, setProblems] = useState<ProblemSummary[]>([]);
  const [members, setMembers] = useState<{ id: string; full_name: string; role: string }[]>([]);
  const [f, setF] = useState({
    title: "", description: "", due: new Date(Date.now() + 7 * 86_400_000).toISOString().slice(0, 10),
    problem_ids: [] as string[], interview_kind: "behavioral", interview_count: 0, assign_all: true, assignee_ids: [] as string[],
  });
  const [error, setError] = useState("");

  useEffect(() => {
    api<ProblemSummary[]>("/problems").then(setProblems);
    api<{ leaderboard: { user_id: string; full_name: string; role: string }[] }>("/analytics/org")
      .then((o) => setMembers(o.leaderboard.filter((m) => m.role === "candidate").map((m) => ({ id: m.user_id, full_name: m.full_name, role: m.role }))));
  }, []);

  const toggle = (key: "problem_ids" | "assignee_ids", id: string) =>
    setF({ ...f, [key]: f[key].includes(id) ? f[key].filter((x) => x !== id) : [...f[key], id] });

  async function save() {
    setError("");
    try {
      await api("/assignments", {
        body: {
          title: f.title, description: f.description, due_at: new Date(`${f.due}T23:59:00`).toISOString(),
          problem_ids: f.problem_ids, interview_kind: f.interview_count ? f.interview_kind : null,
          interview_count: f.interview_count, assign_all: f.assign_all, assignee_ids: f.assign_all ? [] : f.assignee_ids,
        },
      });
      onDone();
    } catch (e: any) {
      setError(e.message);
    }
  }

  return (
    <div className="card stack">
      <h3>New assignment</h3>
      <div className="row wrap">
        <input className="grow" placeholder="Title, e.g. 'Week 3: hash maps'" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
        <label className="small">Due <input type="date" value={f.due} onChange={(e) => setF({ ...f, due: e.target.value })} /></label>
      </div>
      <textarea rows={2} placeholder="Instructions (optional)" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      <b>Problems</b>
      <div className="check-grid">
        {problems.map((p) => (
          <label key={p.id} className="small">
            <input type="checkbox" checked={f.problem_ids.includes(p.id)} onChange={() => toggle("problem_ids", p.id)} />
            {p.title} <span className={`diff ${p.difficulty}`}>{p.difficulty}</span>
          </label>
        ))}
      </div>
      <div className="row wrap">
        <b>Mock interviews</b>
        <input type="number" min={0} max={10} value={f.interview_count} style={{ width: 70 }}
          onChange={(e) => setF({ ...f, interview_count: Number(e.target.value) })} />
        <select value={f.interview_kind} disabled={!f.interview_count} onChange={(e) => setF({ ...f, interview_kind: e.target.value })}>
          <option value="behavioral">behavioral</option>
          <option value="technical">technical</option>
          <option value="system_design">system design</option>
        </select>
      </div>
      <div className="row wrap">
        <b>Assign to</b>
        <label className="small"><input type="radio" checked={f.assign_all} onChange={() => setF({ ...f, assign_all: true })} /> All candidates</label>
        <label className="small"><input type="radio" checked={!f.assign_all} onChange={() => setF({ ...f, assign_all: false })} /> Selected</label>
      </div>
      {!f.assign_all && (
        <div className="check-grid">
          {members.length === 0 && <span className="muted small">No candidates yet - invite some from the Organization page.</span>}
          {members.map((m) => (
            <label key={m.id} className="small">
              <input type="checkbox" checked={f.assignee_ids.includes(m.id)} onChange={() => toggle("assignee_ids", m.id)} /> {m.full_name}
            </label>
          ))}
        </div>
      )}
      {error && <div className="error">{error}</div>}
      <div className="row">
        <button className="primary" onClick={save} disabled={f.title.trim().length < 2}>Create</button>
        <button onClick={onDone}>Cancel</button>
      </div>
    </div>
  );
}

export function AssignmentDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const { user } = useAuth();
  const staff = isStaff(user);
  const [a, setA] = useState<Assignment | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<Assignment>(`/assignments/${id}`).then(setA).catch((e) => setError(e.message));
  }, [id]);

  if (error) return <div className="error">{error}</div>;
  if (!a) return <div className="muted">Loading…</div>;
  const mine = a.my_progress;

  async function remove() {
    if (!confirm(`Delete "${a!.title}"? Progress is computed from submissions, so no practice data is lost.`)) return;
    await api(`/assignments/${id}`, { method: "DELETE" });
    nav("/assignments");
  }

  return (
    <div className="stack">
      <div className="row between wrap">
        <h2>{a.title}</h2>
        <div className="row">
          {mine && <StatusPill status={mine.status} />}
          {staff && <button onClick={() => apiDownload(`/assignments/${id}/export.csv`, "assignment.csv")}>Export CSV</button>}
          {staff && <button onClick={remove}>Delete</button>}
        </div>
      </div>
      <div className="muted">{dueLabel(a.due_at)} · created {new Date(a.created_at).toLocaleDateString()} · only work done after creation counts</div>
      {a.description && <p>{a.description}</p>}

      <div className="card">
        <h3>Tasks</h3>
        {a.problems.map((p) => (
          <div key={p.id} className="row between task-row">
            <span>{mine ? (mine.solved_problem_ids.includes(p.id) ? "✅" : "⬜") : "•"} <Link to={`/problems/${p.id}`}>{p.title}</Link> <span className={`diff ${p.difficulty}`}>{p.difficulty}</span></span>
          </div>
        ))}
        {a.interview_count > 0 && (
          <div className="row between task-row">
            <span>{mine ? (mine.interviews_done >= a.interview_count ? "✅" : "⬜") : "•"} Complete {a.interview_count} {a.interview_kind ?? ""} mock interview{a.interview_count > 1 ? "s" : ""}
              {mine && ` (${mine.interviews_done}/${a.interview_count})`}</span>
            {mine && <Link to={`/interviews?kind=${a.interview_kind ?? "behavioral"}`}>Start →</Link>}
          </div>
        )}
      </div>

      {a.progress && (
        <div className="card">
          <h3>Progress ({a.progress.filter((p) => p.status === "completed" || p.status === "late").length}/{a.progress.length} completed)</h3>
          <table className="table">
            <thead>
              <tr><th>Candidate</th><th>Status</th>
                {a.problems.map((p) => <th key={p.id}>{p.title}</th>)}
                {a.interview_count > 0 && <th>Interviews</th>}
                <th>Completed</th></tr>
            </thead>
            <tbody>
              {a.progress.map((p) => (
                <tr key={p.user_id}>
                  <td><Link to={`/org/members/${p.user_id}`}>{p.full_name}</Link></td>
                  <td><StatusPill status={p.status} /></td>
                  {a.problems.map((pr) => <td key={pr.id}>{p.solved_problem_ids.includes(pr.id) ? "✅" : "—"}</td>)}
                  {a.interview_count > 0 && <td>{p.interviews_done}/{a.interview_count}</td>}
                  <td className="small">{p.completed_at ? new Date(p.completed_at).toLocaleString() : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
