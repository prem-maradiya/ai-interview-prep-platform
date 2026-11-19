import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";

type Progress = {
  solved_by_difficulty: Record<string, number>;
  solved_total: number;
  total_problems: number;
  submissions: number;
  acceptance_rate: number | null;
  interviews_completed: number;
  interview_trend: { id: string; kind: string; score: number; date: string }[];
  focus_areas: { dimension: string; avg_score: number }[];
};

export default function Dashboard() {
  const [p, setP] = useState<Progress | null>(null);
  useEffect(() => {
    api<Progress>("/analytics/me").then(setP);
  }, []);
  if (!p) return <div className="muted">Loading…</div>;

  const max = Math.max(100, ...p.interview_trend.map((t) => t.score));
  return (
    <div className="stack">
      <h2>Your progress</h2>
      <div className="stats">
        <Stat label="Problems solved" value={`${p.solved_total} / ${p.total_problems}`} />
        <Stat label="Submissions" value={p.submissions} />
        <Stat label="Acceptance" value={p.acceptance_rate == null ? "—" : `${p.acceptance_rate}%`} />
        <Stat label="Interviews done" value={p.interviews_completed} />
      </div>
      <div className="grid2">
        <div className="card">
          <h3>Solved by difficulty</h3>
          {["easy", "medium", "hard"].map((d) => (
            <div key={d} className="row between">
              <span className={`diff ${d}`}>{d}</span>
              <b>{p.solved_by_difficulty[d] ?? 0}</b>
            </div>
          ))}
        </div>
        <div className="card">
          <h3>Focus areas</h3>
          {p.focus_areas.length === 0 && <p className="muted">Complete a <Link to="/interviews">mock interview</Link> to see where to improve.</p>}
          {p.focus_areas.map((f) => (
            <div key={f.dimension} className="row between">
              <span>{f.dimension}</span>
              <b>{f.avg_score.toFixed(1)} / 5</b>
            </div>
          ))}
        </div>
      </div>
      <div className="card">
        <h3>Interview score trend</h3>
        {p.interview_trend.length === 0 ? (
          <p className="muted">No completed interviews yet.</p>
        ) : (
          <div className="bars">
            {p.interview_trend.map((t) => (
              <Link key={t.id} to={`/interviews/${t.id}`} className="bar" title={`${t.kind}: ${t.score}`}>
                <div style={{ height: `${(t.score / max) * 100}%` }} />
                <span>{t.score}</span>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card stat">
      <div className="muted small">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}
