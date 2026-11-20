import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ProblemSummary } from "../lib/api";
import { isStaff, useAuth } from "../lib/auth";

type Contest = {
  id: string; title: string; description: string; status: "upcoming" | "running" | "ended";
  starts_at: string; ends_at: string; freeze_minutes: number; freeze_at: string | null; frozen: boolean;
  penalty_minutes: number; problem_count: number;
  problems?: { id: string; label: string; title: string; difficulty: string; my_status: "solved" | "attempted" | null }[];
};
type Board = {
  status: string; frozen: boolean; problems: { id: string; label: string; title: string }[];
  rows: { rank: number; user_id: string; name: string; solved: number; penalty: number;
    problems: Record<string, { attempts: number; solved_minute: number | null; pending: number; first_solve: boolean }> }[];
};

function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

function duration(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return d ? `${d}d ${h}h ${m}m` : h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}:${String(sec).padStart(2, "0")}`;
}

const STATUS_PILL: Record<string, string> = { upcoming: "Upcoming", running: "Live", ended: "Ended" };

/** Contest list, plus the scheduling form for mentors. */
export default function Contests() {
  const { user } = useAuth();
  const [items, setItems] = useState<Contest[] | null>(null);
  const [creating, setCreating] = useState(false);
  const load = () => api<Contest[]>("/contests").then(setItems);
  useEffect(() => { load(); }, []);
  return (
    <div className="stack">
      <div className="row between">
        <h2>Contests</h2>
        {isStaff(user) && !creating && <button className="primary" onClick={() => setCreating(true)}>+ Schedule contest</button>}
      </div>
      <p className="muted">Timed rounds with a live scoreboard: problems solved first, then time. Each rejected attempt on a problem you solve adds a penalty.</p>
      {creating && <ContestForm onDone={() => { setCreating(false); load(); }} />}
      {items === null ? <div className="muted">Loading…</div> : items.length === 0 ? (
        <div className="card muted">No contests yet.{isStaff(user) ? " Schedule one for your cohort." : ""}</div>
      ) : (
        <table className="table">
          <thead><tr><th>Contest</th><th>Status</th><th>Starts</th><th>Length</th><th>Problems</th></tr></thead>
          <tbody>
            {items.map((c) => (
              <tr key={c.id}>
                <td><Link to={`/contests/${c.id}`}>{c.title}</Link></td>
                <td><span className={`pill status-${c.status}`}>{STATUS_PILL[c.status]}</span></td>
                <td className="small">{new Date(c.starts_at).toLocaleString()}</td>
                <td className="small">{duration(new Date(c.ends_at).getTime() - new Date(c.starts_at).getTime())}</td>
                <td>{c.problem_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function localInput(d: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function ContestForm({ onDone }: { onDone: () => void }) {
  const nav = useNavigate();
  const [problems, setProblems] = useState<ProblemSummary[]>([]);
  const [f, setF] = useState({ title: "", description: "", start: localInput(new Date(Date.now() + 10 * 60_000)), minutes: 90, freeze: 15, penalty: 20 });
  const [chosen, setChosen] = useState<string[]>([]);
  const [error, setError] = useState("");
  useEffect(() => { api<ProblemSummary[]>("/problems").then(setProblems); }, []);

  async function save() {
    setError("");
    const starts = new Date(f.start);
    try {
      const c = await api<Contest>("/contests", {
        body: {
          title: f.title, description: f.description, problem_ids: chosen, starts_at: starts.toISOString(),
          ends_at: new Date(starts.getTime() + f.minutes * 60_000).toISOString(), freeze_minutes: f.freeze, penalty_minutes: f.penalty,
        },
      });
      nav(`/contests/${c.id}`);
    } catch (e: any) {
      setError(e.message);
    }
  }

  return (
    <div className="card stack">
      <h3>Schedule a contest</h3>
      <input placeholder="Title, e.g. Friday sprint #3" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
      <textarea rows={2} placeholder="Description / rules (optional)" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      <div className="row wrap">
        <label className="small">Starts <input type="datetime-local" value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} /></label>
        <label className="small">Minutes <input type="number" min={5} max={10080} value={f.minutes} onChange={(e) => setF({ ...f, minutes: +e.target.value })} style={{ width: 90 }} /></label>
        <label className="small">Freeze last <input type="number" min={0} value={f.freeze} onChange={(e) => setF({ ...f, freeze: +e.target.value })} style={{ width: 70 }} /> min</label>
        <label className="small">Penalty <input type="number" min={0} max={120} value={f.penalty} onChange={(e) => setF({ ...f, penalty: +e.target.value })} style={{ width: 70 }} /> min/attempt</label>
      </div>
      <b className="small">Problems ({chosen.length}/12, in order: A, B, C…)</b>
      <div className="problem-picker">
        {problems.map((p) => (
          <label key={p.id} className="small row">
            <input type="checkbox" checked={chosen.includes(p.id)} disabled={!chosen.includes(p.id) && chosen.length >= 12}
              onChange={(e) => setChosen(e.target.checked ? [...chosen, p.id] : chosen.filter((x) => x !== p.id))} />
            {chosen.includes(p.id) && <b>{String.fromCharCode(65 + chosen.indexOf(p.id))}</b>} {p.title}
            <span className={`diff ${p.difficulty}`}>{p.difficulty}</span>
          </label>
        ))}
      </div>
      {error && <div className="error">{error}</div>}
      <div className="row">
        <button className="primary" disabled={!f.title.trim() || chosen.length === 0} onClick={save}>Schedule</button>
        <button onClick={onDone}>Cancel</button>
      </div>
    </div>
  );
}

/** One contest: countdown, problems with your status, and the live scoreboard. */
export function ContestPage() {
  const { id } = useParams();
  const { user } = useAuth();
  const nav = useNavigate();
  const now = useNow();
  const [c, setC] = useState<Contest | null>(null);
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState("");

  const load = () => {
    api<Contest>(`/contests/${id}`).then(setC).catch((e) => setError(e.message));
    api<Board>(`/contests/${id}/scoreboard`).then(setBoard).catch(() => {});
  };
  useEffect(() => {
    load();
    const t = setInterval(load, 15_000); // live scoreboard
    return () => clearInterval(t);
  }, [id]);
  // Reload exactly when it starts or ends, so problems appear on time.
  useEffect(() => {
    if (!c) return;
    const next = [c.starts_at, c.ends_at].map((d) => new Date(d).getTime() - Date.now()).find((ms) => ms > 0);
    if (next !== undefined && next < 2 ** 31) {
      const t = setTimeout(load, next + 500);
      return () => clearTimeout(t);
    }
  }, [c?.status]);

  if (!c) return <div className="muted">{error || "Loading…"}</div>;
  const start = new Date(c.starts_at).getTime(), end = new Date(c.ends_at).getTime();
  const status = now < start ? "upcoming" : now < end ? "running" : "ended";

  return (
    <div className="stack">
      <div className="row between wrap">
        <div>
          <h2 style={{ margin: 0 }}>{c.title} <span className={`pill status-${status}`}>{STATUS_PILL[status]}</span></h2>
          <div className="small muted">
            {new Date(c.starts_at).toLocaleString()} – {new Date(c.ends_at).toLocaleString()} · penalty {c.penalty_minutes} min per rejected attempt
            {c.freeze_minutes > 0 && ` · scoreboard freezes for the last ${c.freeze_minutes} min`}
          </div>
        </div>
        <div className="countdown">
          {status === "upcoming" && <>Starts in <b>{duration(start - now)}</b></>}
          {status === "running" && <>Ends in <b>{duration(end - now)}</b></>}
          {status === "ended" && <>Final results</>}
        </div>
      </div>
      {c.description && <div className="card">{c.description}</div>}
      {isStaff(user) && (
        <div><button onClick={async () => { if (confirm("Delete this contest? Submissions are kept.")) { await api(`/contests/${c.id}`, { method: "DELETE" }); nav("/contests"); } }}>Delete contest</button></div>
      )}

      <div className="card stack">
        <h3>Problems</h3>
        {c.problems && c.problems.length > 0 ? c.problems.map((p) => (
          <div key={p.id} className="row between task-row">
            <span><b>{p.label}.</b> <Link to={`/problems/${p.id}`}>{p.title}</Link> <span className={`diff ${p.difficulty}`}>{p.difficulty}</span></span>
            <span className="small">{p.my_status === "solved" ? "✅ solved" : p.my_status === "attempted" ? "❌ attempted" : ""}</span>
          </div>
        )) : <div className="muted small">The problems are revealed when the contest starts.</div>}
        {status === "running" && <div className="small muted">Open a problem and submit as usual: submissions count automatically while the contest runs.</div>}
      </div>

      <div className="card stack">
        <div className="row between">
          <h3>Scoreboard</h3>
          {board?.frozen && <span className="pill">❄ frozen: others' last-minute results are hidden until the end</span>}
        </div>
        {!board || board.rows.length === 0 ? <div className="muted small">No accepted or rejected submissions yet.</div> : (
          <table className="table scoreboard">
            <thead>
              <tr><th>#</th><th>Name</th><th>Solved</th><th>Penalty</th>{board.problems.map((p) => <th key={p.id} title={p.title}>{p.label}</th>)}</tr>
            </thead>
            <tbody>
              {board.rows.map((r) => (
                <tr key={r.user_id} className={r.user_id === user?.id ? "me" : ""}>
                  <td>{r.rank}</td><td>{r.name}</td><td><b>{r.solved}</b></td><td>{r.penalty}</td>
                  {board.problems.map((p) => {
                    const cell = r.problems[p.id];
                    if (!cell) return <td key={p.id}></td>;
                    if (cell.solved_minute !== null) {
                      return <td key={p.id} className={`cell solved ${cell.first_solve ? "first" : ""}`} title={cell.first_solve ? "First to solve" : ""}>
                        +{cell.attempts || ""}<div className="small">{cell.solved_minute}′</div></td>;
                    }
                    if (cell.pending) return <td key={p.id} className="cell pending">?{cell.attempts + cell.pending}</td>;
                    return <td key={p.id} className="cell failed">−{cell.attempts}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
