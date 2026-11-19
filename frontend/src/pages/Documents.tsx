import { useEffect, useState } from "react";
import { api, apiUpload, type DocumentSummary, type FitReport } from "../lib/api";

type Kind = "resume" | "job_description";
const LABEL: Record<Kind, string> = { resume: "Resume", job_description: "Job description" };

export default function Documents() {
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [fit, setFit] = useState<FitReport | null>(null);
  const [pick, setPick] = useState({ resume: "", jd: "" });
  const [busy, setBusy] = useState(false);

  const load = () =>
    api<DocumentSummary[]>("/documents")
      .then((d) => { setDocs(d); setLoaded(true); })
      .catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  const resumes = docs.filter((d) => d.kind === "resume");
  const jds = docs.filter((d) => d.kind === "job_description");

  async function remove(id: string) {
    await api(`/documents/${id}`, { method: "DELETE" });
    load();
  }

  async function analyze() {
    setBusy(true);
    setError("");
    setFit(null);
    try {
      setFit(await api<FitReport>("/documents/fit", { body: { resume_id: pick.resume, job_description_id: pick.jd } }));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h2>Resume &amp; target jobs</h2>
      <p className="muted">Add your resume and the job descriptions you're applying for. Interviews can then be tailored to them, and the fit report shows your gaps. Only you can see these.</p>
      <div className="grid2">
        <AddDocument kind="resume" onAdded={load} />
        <AddDocument kind="job_description" onAdded={load} />
      </div>

      <div className="card">
        <h3>Your documents</h3>
        {!loaded && <p className="muted">Loading…</p>}
        {loaded && docs.length === 0 && <p className="muted">Nothing added yet.</p>}
        {docs.map((d) => (
          <div key={d.id} className="row between doc-row">
            <div>
              <span className="pill">{LABEL[d.kind]}</span> <b>{d.title}</b>
              <div className="small muted">{d.preview}…</div>
            </div>
            <button className="link" onClick={() => remove(d.id)}>Delete</button>
          </div>
        ))}
      </div>

      <div className="card stack">
        <h3>Resume ↔ job fit report</h3>
        <div className="row wrap">
          <select value={pick.resume} onChange={(e) => setPick({ ...pick, resume: e.target.value })}>
            <option value="">Choose resume…</option>
            {resumes.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
          </select>
          <select value={pick.jd} onChange={(e) => setPick({ ...pick, jd: e.target.value })}>
            <option value="">Choose job description…</option>
            {jds.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
          </select>
          <button className="primary" disabled={!pick.resume || !pick.jd || busy} onClick={analyze}>
            {busy ? "Analyzing…" : "Analyze fit"}
          </button>
        </div>
        {error && <div className="error">{error}</div>}
        {fit && (
          <div className="stack">
            <div className="row between">
              <p>{fit.summary}</p>
              <span className="stat-value">{fit.match_score}%</span>
            </div>
            <div className="grid2">
              <div><h4>Matched</h4>{fit.matched_skills.map((s) => <span key={s} className="tag ok">{s}</span>)}</div>
              <div><h4>Missing</h4>{fit.missing_skills.map((s) => <span key={s} className="tag bad">{s}</span>)}</div>
            </div>
            <div className="grid2">
              <div><h4>Resume suggestions</h4><ul>{fit.resume_suggestions.map((s) => <li key={s}>{s}</li>)}</ul></div>
              <div><h4>Questions to prepare</h4><ul>{fit.likely_questions.map((s) => <li key={s}>{s}</li>)}</ul></div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function AddDocument({ kind, onAdded }: { kind: Kind; onAdded: () => void }) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      setTitle("");
      setText("");
      onAdded();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  function upload(file: File) {
    const form = new FormData();
    form.append("kind", kind);
    form.append("file", file);
    if (title) form.append("title", title);
    run(() => apiUpload("/documents/upload", form));
  }

  return (
    <div className="card stack">
      <h3>Add {LABEL[kind].toLowerCase()}</h3>
      <input placeholder={kind === "resume" ? "Title, e.g. 'Resume - backend'" : "Title, e.g. 'Acme - Backend Engineer'"}
        value={title} onChange={(e) => setTitle(e.target.value)} />
      <label className="file-drop">
        <input type="file" accept=".pdf,.txt,.md" disabled={busy}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
        {busy ? "Uploading…" : "Upload PDF / TXT / MD (max 2 MB)"}
      </label>
      <div className="muted small">…or paste the text:</div>
      <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)}
        placeholder={kind === "resume" ? "Paste your resume" : "Paste the job description"} />
      {error && <div className="error">{error}</div>}
      <div>
        <button disabled={busy || text.trim().length < 30 || !title.trim()}
          onClick={() => run(() => api("/documents", { body: { kind, title: title.trim(), text } }))}>
          Save pasted text
        </button>
      </div>
    </div>
  );
}
