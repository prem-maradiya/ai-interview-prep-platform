import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type ProblemSummary } from "../lib/api";
import { isStaff, useAuth } from "../lib/auth";
import ProblemEditor from "../components/ProblemEditor";
import { ProblemListsGrid } from "./ProblemLists";

type Summary = ProblemSummary & { companies: string[] };

export default function Problems() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "lists" ? "lists" : "all";
  const [items, setItems] = useState<Summary[] | null>(null);
  const [difficulty, setDifficulty] = useState("");
  const [company, setCompany] = useState("");
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);

  const load = () => api<Summary[]>(`/problems${difficulty ? `?difficulty=${difficulty}` : ""}`).then(setItems);
  useEffect(() => {
    load();
  }, [difficulty]);

  const companies = useMemo(() => [...new Set((items ?? []).flatMap((p) => p.companies))].sort(), [items]);
  const shown = (items ?? []).filter((p) =>
    (!company || p.companies.includes(company)) &&
    (!query || p.title.toLowerCase().includes(query.toLowerCase()) || p.tags.some((t) => t.includes(query.toLowerCase()))));

  return (
    <div className="stack">
      <div className="row between wrap">
        <div className="row">
          <h2>Problems</h2>
          <div className="tabs inline">
            <button className={view === "all" ? "active" : ""} onClick={() => setParams({})}>All problems</button>
            <button className={view === "lists" ? "active" : ""} onClick={() => setParams({ view: "lists" })}>Curated lists</button>
          </div>
        </div>
        {view === "all" && (
          <div className="row wrap">
            <input placeholder="Search title or topic" value={query} onChange={(e) => setQuery(e.target.value)} />
            <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
              <option value="">All difficulties</option>
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
            </select>
            <select value={company} onChange={(e) => setCompany(e.target.value)}>
              <option value="">All companies</option>
              {companies.map((c) => <option key={c}>{c}</option>)}
            </select>
            {isStaff(user) && <button className="primary" onClick={() => setCreating(true)}>+ New problem</button>}
          </div>
        )}
      </div>

      {view === "lists" ? <ProblemListsGrid /> : (
        <>
          {creating && <ProblemEditor onDone={() => { setCreating(false); load(); }} />}
          {items === null ? <p className="muted">Loading…</p> : (
            <table className="table">
              <thead>
                <tr><th></th><th>Title</th><th>Difficulty</th><th>Topics</th><th>Asked at</th><th>Source</th></tr>
              </thead>
              <tbody>
                {shown.map((p) => (
                  <tr key={p.id}>
                    <td>{p.solved ? "✅" : ""}</td>
                    <td>
                      <Link to={`/problems/${p.id}`}>{p.title}</Link>
                      {p.style === "function" && <span className="tag fn-tag" title="Write a function; the judge calls it">ƒ function</span>}
                    </td>
                    <td><span className={`diff ${p.difficulty}`}>{p.difficulty}</span></td>
                    <td>{p.tags.map((t) => <span key={t} className="tag">{t}</span>)}</td>
                    <td className="small muted">{p.companies.join(", ")}</td>
                    <td className="muted small">{p.is_global ? "Global" : "Your org"}</td>
                  </tr>
                ))}
                {shown.length === 0 && <tr><td colSpan={6} className="muted">No problems match these filters.</td></tr>}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}
