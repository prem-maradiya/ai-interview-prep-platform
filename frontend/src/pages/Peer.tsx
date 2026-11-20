import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../lib/api";

type Person = { id: string; name: string } | null;
type Feedback = { round: number; problem_solving: number; communication: number; code_quality: number; hire: string; comments: string };
export type PeerSummary = {
  id: string; status: string; round: number; host: Person; partner: Person; invited_id: string | null;
  created_at: string; ended_at: string | null; my_feedback: Feedback[];
};
type Member = { id: string; name: string; role: string };

export const HIRE_LABEL: Record<string, string> = { strong_no: "Strong no", no: "No", yes: "Yes", strong_yes: "Strong yes" };

/** Peer practice lobby: open rooms to join, start a room, and past peer interviews with feedback received. */
export default function Peer() {
  const nav = useNavigate();
  const [data, setData] = useState<{ open: PeerSummary[]; mine: PeerSummary[] } | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [form, setForm] = useState({ invite_user_id: "", difficulty: "", host_first: true });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api<{ open: PeerSummary[]; mine: PeerSummary[] }>("/peer").then(setData).catch((e) => setError(e.message));
  useEffect(() => {
    load();
    api<Member[]>("/peer/members").then(setMembers).catch(() => {});
    const t = setInterval(load, 10_000); // new open rooms appear without a reload
    return () => clearInterval(t);
  }, []);

  async function start() {
    setBusy(true);
    setError("");
    try {
      const r = await api<{ id: string }>("/peer", {
        body: { invite_user_id: form.invite_user_id || null, difficulty: form.difficulty || null, host_first: form.host_first },
      });
      nav(`/peer/${r.id}`);
    } catch (e: any) {
      setError(e.message);
      setBusy(false);
    }
  }

  async function join(id: string) {
    setError("");
    try {
      await api(`/peer/${id}/join`, { method: "POST" });
      nav(`/peer/${id}`);
    } catch (e: any) {
      setError(e.message);
      load();
    }
  }

  return (
    <div className="stack">
      <h2>Peer mock interviews</h2>
      <p className="muted">
        Practise with a teammate: one of you interviews while the other solves a problem live, then you swap.
        You share the editor, chat and test runs in real time, and rate each other at the end.
      </p>
      {error && <div className="error">{error}</div>}

      <div className="card stack">
        <h3>Start a room</h3>
        <div className="row wrap">
          <select value={form.invite_user_id} onChange={(e) => setForm({ ...form, invite_user_id: e.target.value })}>
            <option value="">Open to anyone in my organization</option>
            {members.map((m) => <option key={m.id} value={m.id}>Invite {m.name} ({m.role})</option>)}
          </select>
          <select value={form.difficulty} onChange={(e) => setForm({ ...form, difficulty: e.target.value })}>
            <option value="">Any difficulty</option>
            <option value="easy">Easy</option><option value="medium">Medium</option><option value="hard">Hard</option>
          </select>
          <select value={form.host_first ? "solve" : "interview"} onChange={(e) => setForm({ ...form, host_first: e.target.value === "solve" })}>
            <option value="solve">I solve first</option>
            <option value="interview">I interview first</option>
          </select>
          <button className="primary" onClick={start} disabled={busy}>{busy ? "Starting…" : "Start room"}</button>
        </div>
      </div>

      <div className="card stack">
        <h3>Open rooms</h3>
        {data === null ? <div className="muted small">Loading…</div> : data.open.length === 0 ? (
          <div className="muted small">No open rooms right now. Start one, and teammates will see it here.</div>
        ) : data.open.map((r) => (
          <div key={r.id} className="row between task-row">
            <span><b>{r.host?.name}</b> {r.invited_id ? "invited you" : "is looking for a partner"} · <span className="muted small">{new Date(r.created_at).toLocaleTimeString()}</span></span>
            <button className="primary" onClick={() => join(r.id)}>Join</button>
          </div>
        ))}
      </div>

      <div className="card stack">
        <h3>Your peer interviews</h3>
        {data === null ? <div className="muted small">Loading…</div> : data.mine.length === 0 ? (
          <div className="muted small">None yet.</div>
        ) : <PeerHistory items={data.mine} />}
      </div>
    </div>
  );
}

export function PeerHistory({ items }: { items: PeerSummary[] }) {
  return (
    <table className="table">
      <thead><tr><th>When</th><th>Partner</th><th>Status</th><th>Feedback received</th></tr></thead>
      <tbody>
        {items.map((s) => (
          <tr key={s.id}>
            <td><Link to={`/peer/${s.id}`}>{new Date(s.created_at).toLocaleDateString()}</Link></td>
            <td>{s.partner?.name ?? <span className="muted">waiting…</span>}</td>
            <td><span className="pill">{s.status}</span></td>
            <td className="small">
              {s.my_feedback.length === 0 ? <span className="muted">—</span> : s.my_feedback.map((f) => (
                <div key={f.round}>
                  {HIRE_LABEL[f.hire]} · solving {f.problem_solving}/5 · communication {f.communication}/5 · code {f.code_quality}/5
                </div>
              ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
