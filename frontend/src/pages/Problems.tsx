import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ProblemSummary } from "../lib/api";
import { isStaff, useAuth } from "../lib/auth";
import ProblemEditor from "../components/ProblemEditor";

export default function Problems() {
  const { user } = useAuth();
  const [items, setItems] = useState<ProblemSummary[]>([]);
  const [difficulty, setDifficulty] = useState("");
  const [creating, setCreating] = useState(false);

  const load = () => api<ProblemSummary[]>(`/problems${difficulty ? `?difficulty=${difficulty}` : ""}`).then(setItems);
  useEffect(() => {
    load();
  }, [difficulty]);

  return (
    <div className="stack">
      <div className="row between">
        <h2>Problems</h2>
        <div className="row">
          <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
            <option value="">All difficulties</option>
            <option value="easy">Easy</option>
            <option value="medium">Medium</option>
            <option value="hard">Hard</option>
          </select>
          {isStaff(user) && <button className="primary" onClick={() => setCreating(true)}>+ New problem</button>}
        </div>
      </div>
      {creating && <ProblemEditor onDone={() => { setCreating(false); load(); }} />}
      <table className="table">
        <thead>
          <tr><th></th><th>Title</th><th>Difficulty</th><th>Tags</th><th>Source</th></tr>
        </thead>
        <tbody>
          {items.map((p) => (
            <tr key={p.id}>
              <td>{p.solved ? "✅" : ""}</td>
              <td><Link to={`/problems/${p.id}`}>{p.title}</Link></td>
              <td><span className={`diff ${p.difficulty}`}>{p.difficulty}</span></td>
              <td>{p.tags.map((t) => <span key={t} className="tag">{t}</span>)}</td>
              <td className="muted small">{p.is_global ? "Global" : "Your org"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
