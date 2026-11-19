import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { api, type IntegrityFlag, type Problem, type Submission } from "../lib/api";
import CommentThread from "../components/CommentThread";

export default function SubmissionView() {
  const { id } = useParams();
  const [sub, setSub] = useState<Submission | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Submission>(`/submissions/${id}`)
      .then((s) => {
        setSub(s);
        return api<Problem>(`/problems/${s.problem_id}`).then(setProblem);
      })
      .catch((e) => setError(e.message));
  }, [id]);

  if (error) return <div className="error">{error}</div>;
  if (!sub) return <div className="muted">Loading…</div>;

  return (
    <div className="stack">
      <div className="row between wrap">
        <h2>Submission · {problem ? <Link to={`/problems/${problem.id}`}>{problem.title}</Link> : "…"}</h2>
        <span className={`verdict ${sub.verdict}`}>{sub.verdict.replace(/_/g, " ")} · {sub.passed}/{sub.total} · {sub.language}</span>
      </div>
      <div className="muted small">{new Date(sub.created_at).toLocaleString()}</div>
      {sub.integrity_flags && sub.integrity_flags.length > 0 && <Flags flags={sub.integrity_flags} />}
      <pre className="code-view">{sub.code}</pre>
      {sub.ai_review && <div className="card prose"><h4>AI code review</h4><Markdown>{sub.ai_review}</Markdown></div>}
      <CommentThread targetType="submission" targetId={sub.id} />
    </div>
  );
}

const SEVERITY_ORDER = { high: 0, medium: 1, low: 2 };

export function Flags({ flags }: { flags: IntegrityFlag[] }) {
  const sorted = [...flags].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
  return (
    <div className="card flags">
      <b>Integrity signals</b> <span className="small muted">(hints to look closer at, not proof)</span>
      <ul>
        {sorted.map((f) => (
          <li key={f.type}><span className={`pill sev-${f.severity}`}>{f.severity}</span> {f.detail}</li>
        ))}
      </ul>
    </div>
  );
}
