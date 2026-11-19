import { useEffect, useState } from "react";
import { api } from "../lib/api";

export type Achievements = {
  level: number; xp: number; current_level_xp: number; next_level_xp: number | null; streak_days: number;
  badges: { key: string; name: string; description: string; icon: string; earned: boolean; earned_at: string | null }[];
};

export function LevelBar({ a }: { a: Achievements }) {
  const span = a.next_level_xp ? a.next_level_xp - a.current_level_xp : 1;
  const pct = a.next_level_xp ? Math.round(((a.xp - a.current_level_xp) / span) * 100) : 100;
  return (
    <div>
      <div className="row between small">
        <b>Level {a.level}</b>
        <span className="muted">{a.next_level_xp ? `${a.xp} / ${a.next_level_xp} XP` : `${a.xp} XP · max level`}</span>
      </div>
      <div className="progress"><div style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

export default function AchievementsPage() {
  const [a, setA] = useState<Achievements | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<Achievements>("/achievements").then(setA).catch((e) => setError(e.message));
  }, []);
  if (error) return <div className="error">{error}</div>;
  if (!a) return <div className="muted">Loading…</div>;
  const earned = a.badges.filter((b) => b.earned).length;

  return (
    <div className="stack">
      <h2>Achievements</h2>
      <div className="card stack">
        <LevelBar a={a} />
        <div className="small muted">
          XP comes from first solves (easy 10 · medium 25 · hard 50), completed mock interviews (20 + score/10) and
          on-time spaced-repetition reviews (5). 🔥 Current streak: {a.streak_days} day{a.streak_days === 1 ? "" : "s"}.
        </div>
      </div>
      <h3>Badges ({earned}/{a.badges.length})</h3>
      <div className="badge-grid">
        {a.badges.map((b) => (
          <div key={b.key} className={`card badge ${b.earned ? "earned" : "locked"}`} title={b.description}>
            <div className="badge-icon">{b.icon}</div>
            <b>{b.name}</b>
            <div className="small muted">{b.description}</div>
            {b.earned_at && <div className="small">Earned {new Date(b.earned_at).toLocaleDateString()}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
