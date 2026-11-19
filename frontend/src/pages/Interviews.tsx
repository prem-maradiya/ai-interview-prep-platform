import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Interview } from "../lib/api";

type Summary = Omit<Interview, "messages" | "scorecard" | "focus">;

export default function Interviews() {
  const nav = useNavigate();
  const [items, setItems] = useState<Summary[]>([]);
  const [form, setForm] = useState({ kind: "behavioral", role_title: "Software Engineer", level: "junior", focus: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Summary[]>("/interviews").then(setItems);
  }, []);

  async function start() {
    setBusy(true);
    setError("");
    try {
      const iv = await api<Interview>("/interviews", { body: form });
      nav(`/interviews/${iv.id}`);
    } catch (e: any) {
      setError(e.message);
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h2>Mock interviews</h2>
      <div className="card stack">
        <h3>Start a new interview</h3>
        <div className="row wrap">
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            <option value="behavioral">Behavioral</option>
            <option value="technical">Technical (DSA)</option>
            <option value="system_design">System design</option>
          </select>
          <input value={form.role_title} onChange={(e) => setForm({ ...form, role_title: e.target.value })} placeholder="Role" />
          <select value={form.level} onChange={(e) => setForm({ ...form, level: e.target.value })}>
            {["intern", "junior", "mid", "senior", "staff"].map((l) => <option key={l}>{l}</option>)}
          </select>
        </div>
        <input value={form.focus} onChange={(e) => setForm({ ...form, focus: e.target.value })}
          placeholder="Optional focus, e.g. 'leadership', 'graphs', 'payments at scale'" />
        {error && <div className="error">{error}</div>}
        <div><button className="primary" onClick={start} disabled={busy}>{busy ? "Starting…" : "Start interview"}</button></div>
      </div>
      <table className="table">
        <thead><tr><th>Date</th><th>Type</th><th>Role</th><th>Status</th><th>Score</th></tr></thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.id}>
              <td><Link to={`/interviews/${i.id}`}>{new Date(i.created_at).toLocaleString()}</Link></td>
              <td>{i.kind.replace("_", " ")}</td>
              <td>{i.level} {i.role_title}</td>
              <td><span className="pill">{i.status}</span></td>
              <td>{i.overall_score ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
