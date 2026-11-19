import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Plan, type PlanItem } from "../lib/api";
import { LevelBar, type Achievements } from "./Achievements";

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
  const [plan, setPlan] = useState<Plan | null>(null);
  const [ach, setAch] = useState<Achievements | null>(null);
  useEffect(() => {
    api<Progress>("/analytics/me").then(setP);
    api<Plan>("/practice/plan").then(setPlan);
    api<Achievements>("/achievements").then(setAch).catch(() => {});
  }, []);
  if (!p) return <div className="muted">Loading…</div>;

  const max = Math.max(100, ...p.interview_trend.map((t) => t.score));
  return (
    <div className="stack">
      {plan && <TodayPlan plan={plan} />}
      {ach && (
        <Link to="/achievements" className="card level-card">
          <LevelBar a={ach} />
          <div className="small muted">{ach.badges.filter((b) => b.earned).map((b) => b.icon).join(" ") || "No badges yet - solve a problem to earn your first."}</div>
        </Link>
      )}
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

function TodayPlan({ plan }: { plan: Plan }) {
  const s = plan.suggested_interview;
  const interviewLink = `/interviews?kind=${s.kind}&focus=${encodeURIComponent(s.focus)}`;
  return (
    <div className="card stack plan">
      <div className="row between wrap">
        <h2>Today's plan</h2>
        <span className={`streak ${plan.practiced_today ? "lit" : ""}`} title="Consecutive days with practice">
          🔥 {plan.streak_days}-day streak{!plan.practiced_today && plan.streak_days > 0 ? " · practice today to keep it" : ""}
        </span>
      </div>
      <div className="grid3">
        <div>
          <h4>Due for review {plan.due_count > 0 && <span className="pill">{plan.due_count}</span>}</h4>
          {plan.due_reviews.length === 0 ? (
            <p className="muted small">
              Nothing due.{plan.next_review_at ? ` Next review ${new Date(plan.next_review_at).toLocaleDateString()}.` : " Solve problems to start your review schedule."}
            </p>
          ) : (
            plan.due_reviews.map((r) => <PlanRow key={r.problem_id} item={r} />)
          )}
        </div>
        <div>
          <h4>New problems</h4>
          {plan.new_problems.length === 0 ? <p className="muted small">You've attempted every problem. 🎉</p>
            : plan.new_problems.map((r) => <PlanRow key={r.problem_id} item={r} />)}
        </div>
        <div>
          <h4>Mock interview</h4>
          <p className="small">{s.reason}.</p>
          <Link to={interviewLink} className="button-link">Start {s.kind.replace("_", " ")} interview →</Link>
        </div>
      </div>
      <div className="small muted">{plan.learned_count} problems in your spaced-repetition rotation.</div>
    </div>
  );
}

function PlanRow({ item }: { item: PlanItem }) {
  return (
    <div className="plan-row">
      <Link to={`/problems/${item.problem_id}`}>{item.title}</Link> <span className={`diff ${item.difficulty}`}>{item.difficulty}</span>
      <div className="small muted">{item.reason}{item.overdue_days ? ` · ${item.overdue_days}d overdue` : ""}</div>
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
