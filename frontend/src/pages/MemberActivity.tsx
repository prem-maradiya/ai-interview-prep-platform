import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type IntegrityFlag } from "../lib/api";

type Activity = {
  member: { id: string; full_name: string; email: string; role: string };
  submissions: { id: string; problem_id: string; problem_title: string; language: string; verdict: string; passed: number;
    total: number; created_at: string; integrity_flags: IntegrityFlag[] | null }[];
  interviews: { id: string; kind: string; role_title: string; level: string; status: string; overall_score: number | null; created_at: string }[];
};

export default function MemberActivity() {
  const { id } = useParams();
  const [a, setA] = useState<Activity | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<Activity>(`/org/members/${id}/activity`).then(setA).catch((e) => setError(e.message));
  }, [id]);
  if (error) return <div className="error">{error}</div>;
  if (!a) return <div className="muted">Loading…</div>;

  return (
    <div className="stack">
      <h2>{a.member.full_name} <span className="pill">{a.member.role}</span></h2>
      <div className="muted">{a.member.email}</div>
      <div className="grid2">
        <div className="card">
          <h3>Interviews</h3>
          {a.interviews.length === 0 && <p className="muted small">None yet.</p>}
          {a.interviews.map((i) => (
            <div key={i.id} className="row between task-row">
              <Link to={`/interviews/${i.id}`}>{i.kind.replace("_", " ")} · {i.level} {i.role_title}</Link>
              <span className="small muted">{i.status === "completed" ? `${i.overall_score}/100` : "in progress"} · {new Date(i.created_at).toLocaleDateString()}</span>
            </div>
          ))}
        </div>
        <div className="card">
          <h3>Submissions</h3>
          {a.submissions.length === 0 && <p className="muted small">None yet.</p>}
          {a.submissions.map((s) => (
            <div key={s.id} className="task-row">
              <div className="row between">
                <Link to={`/submissions/${s.id}`}>{s.problem_title}</Link>
                <span className="small"><span className={`verdict ${s.verdict}`}>{s.verdict.replace(/_/g, " ")}</span> · {s.passed}/{s.total} · {s.language}</span>
              </div>
              {s.integrity_flags?.map((f) => (
                <div key={f.type} className="small"><span className={`pill sev-${f.severity}`}>{f.severity}</span> {f.detail}</div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
