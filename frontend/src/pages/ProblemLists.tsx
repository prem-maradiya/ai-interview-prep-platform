import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ProblemSummary } from "../lib/api";
import { isStaff, useAuth } from "../lib/auth";

type ListSummary = {
  id: string; slug: string; title: string; description: string; is_global: boolean; total: number; solved: number;
  difficulties: Record<string, number>;
};
type ListDetail = ListSummary & { problems: (ProblemSummary & { companies: string[] })[] };

export function ProblemListsGrid() {
  const { user } = useAuth();
  const [lists, setLists] = useState<ListSummary[] | null>(null);
  const [creating, setCreating] = useState(false);
  const load = () => api<ListSummary[]>("/lists").then(setLists);
  useEffect(() => {
    load();
  }, []);
  if (!lists) return <p className="muted">Loading…</p>;

  return (
    <div className="stack">
      {isStaff(user) && (
        <div><button onClick={() => setCreating(!creating)}>{creating ? "Cancel" : "+ New list for your org"}</button></div>
      )}
      {creating && <NewList onDone={() => { setCreating(false); load(); }} />}
      <div className="list-grid">
        {lists.map((l) => (
          <Link key={l.id} to={`/lists/${l.id}`} className="card list-card">
            <div className="row between">
              <b>{l.title}</b>
              <span className="pill">{l.is_global ? "Curated" : "Your org"}</span>
            </div>
            <div className="small muted">{l.description}</div>
            <div className="small">
              {(["easy", "medium", "hard"] as const).filter((d) => l.difficulties[d]).map((d) => (
                <span key={d} className={`diff ${d}`}>{l.difficulties[d]} {d} </span>
              ))}
            </div>
            <div className="progress"><div style={{ width: `${l.total ? (l.solved / l.total) * 100 : 0}%` }} /></div>
            <div className="small muted">{l.solved}/{l.total} solved</div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function NewList({ onDone }: { onDone: () => void }) {
  const [problems, setProblems] = useState<ProblemSummary[]>([]);
  const [f, setF] = useState({ title: "", description: "", ids: [] as string[] });
  const [error, setError] = useState("");
  useEffect(() => {
    api<ProblemSummary[]>("/problems").then(setProblems);
  }, []);
  const toggle = (id: string) => setF({ ...f, ids: f.ids.includes(id) ? f.ids.filter((x) => x !== id) : [...f.ids, id] });

  async function save() {
    try {
      await api("/lists", { body: { title: f.title, description: f.description, problem_ids: f.ids } });
      onDone();
    } catch (e: any) {
      setError(e.message);
    }
  }

  return (
    <div className="card stack">
      <input placeholder="List title, e.g. 'Week 3: graphs'" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
      <input placeholder="Description (optional)" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      <div className="check-grid">
        {problems.map((p) => (
          <label key={p.id} className="small">
            <input type="checkbox" checked={f.ids.includes(p.id)} onChange={() => toggle(p.id)} /> {p.title}{" "}
            <span className={`diff ${p.difficulty}`}>{p.difficulty}</span>
          </label>
        ))}
      </div>
      {error && <div className="error">{error}</div>}
      <div><button className="primary" disabled={f.title.trim().length < 2 || !f.ids.length} onClick={save}>Create list</button></div>
    </div>
  );
}

export default function ProblemListPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { user } = useAuth();
  const [l, setL] = useState<ListDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<ListDetail>(`/lists/${id}`).then(setL).catch((e) => setError(e.message));
  }, [id]);
  if (error) return <div className="error">{error}</div>;
  if (!l) return <div className="muted">Loading…</div>;

  async function remove() {
    if (!confirm(`Delete the list "${l!.title}"? Problems and submissions are not affected.`)) return;
    await api(`/lists/${id}`, { method: "DELETE" });
    nav("/problems?view=lists");
  }

  return (
    <div className="stack">
      <div className="row between wrap">
        <h2>{l.title}</h2>
        <div className="row">
          <span className="muted">{l.solved}/{l.total} solved</span>
          {!l.is_global && isStaff(user) && <button onClick={remove}>Delete list</button>}
        </div>
      </div>
      {l.description && <p className="muted">{l.description}</p>}
      <div className="progress"><div style={{ width: `${l.total ? (l.solved / l.total) * 100 : 0}%` }} /></div>
      <table className="table">
        <thead><tr><th></th><th>Problem</th><th>Difficulty</th><th>Topics</th><th>Asked at</th></tr></thead>
        <tbody>
          {l.problems.map((p) => (
            <tr key={p.id}>
              <td>{p.solved ? "✅" : ""}</td>
              <td><Link to={`/problems/${p.id}`}>{p.title}</Link></td>
              <td><span className={`diff ${p.difficulty}`}>{p.difficulty}</span></td>
              <td>{p.tags.map((t) => <span key={t} className="tag">{t}</span>)}</td>
              <td className="small muted">{p.companies.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
