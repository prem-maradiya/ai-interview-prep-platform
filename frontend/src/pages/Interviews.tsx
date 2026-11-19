import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type DocumentSummary, type Interview } from "../lib/api";

type Summary = Omit<Interview, "messages" | "scorecard" | "focus">;

export default function Interviews() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [items, setItems] = useState<Summary[]>([]);
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [form, setForm] = useState({
    kind: params.get("kind") ?? "behavioral",
    role_title: "Software Engineer",
    level: "junior",
    focus: params.get("focus") ?? "",
    resume_id: "",
    job_description_id: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Summary[]>("/interviews").then(setItems);
    api<DocumentSummary[]>("/documents").then(setDocs);
  }, []);

  async function start() {
    setBusy(true);
    setError("");
    try {
      const iv = await api<Interview>("/interviews", {
        body: { ...form, resume_id: form.resume_id || null, job_description_id: form.job_description_id || null },
      });
      nav(`/interviews/${iv.id}`);
    } catch (e: any) {
      setError(e.message);
      setBusy(false);
    }
  }

  const resumes = docs.filter((d) => d.kind === "resume");
  const jds = docs.filter((d) => d.kind === "job_description");

  return (
    <div className="stack">
      <h2>Mock interviews</h2>
      <div className="card stack">
        <h3>Start a new interview</h3>
        <div className="row wrap">
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            <option value="behavioral">Behavioral</option>
            <option value="technical">Technical (DSA, with code editor)</option>
            <option value="system_design">System design</option>
          </select>
          <input value={form.role_title} onChange={(e) => setForm({ ...form, role_title: e.target.value })} placeholder="Role" />
          <select value={form.level} onChange={(e) => setForm({ ...form, level: e.target.value })}>
            {["intern", "junior", "mid", "senior", "staff"].map((l) => <option key={l}>{l}</option>)}
          </select>
        </div>
        <input value={form.focus} onChange={(e) => setForm({ ...form, focus: e.target.value })}
          placeholder="Optional focus, e.g. 'leadership', 'graphs', 'payments at scale'" />
        <div className="row wrap">
          <span className="small muted">Tailor to:</span>
          <select value={form.resume_id} onChange={(e) => setForm({ ...form, resume_id: e.target.value })}>
            <option value="">No resume</option>
            {resumes.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
          </select>
          <select value={form.job_description_id} onChange={(e) => setForm({ ...form, job_description_id: e.target.value })}>
            <option value="">No job description</option>
            {jds.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
          </select>
          {docs.length === 0 && <Link to="/documents" className="small">Add your resume or a job description</Link>}
        </div>
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
