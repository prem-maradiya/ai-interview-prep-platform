import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { isAdmin, useAuth } from "../lib/auth";

type Member = { id: string; email: string; full_name: string; role: string; is_active: boolean };
type Invite = { id: string; code: string; email: string | null; role: string; expires_at: string; used_at: string | null };
type Usage = Record<string, { used: number; limit: number | null; window_days: number }>;
type Row = { user_id: string; full_name: string; role: string; problems_solved: number; submissions: number; interviews_completed: number; avg_interview_score: number | null };

export default function OrgAdmin() {
  const { user } = useAuth();
  const admin = isAdmin(user);
  const [usage, setUsage] = useState<Usage>({});
  const [board, setBoard] = useState<Row[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [inv, setInv] = useState({ email: "", role: "candidate" });
  const [error, setError] = useState("");

  const load = async () => {
    const org = await api<{ usage: Usage }>("/org");
    setUsage(org.usage);
    setBoard((await api<{ leaderboard: Row[] }>("/analytics/org")).leaderboard);
    if (admin) {
      setMembers(await api<Member[]>("/org/members"));
      setInvites(await api<Invite[]>("/org/invites"));
    }
  };
  useEffect(() => {
    load();
  }, []);

  async function invite() {
    setError("");
    try {
      await api("/org/invites", { body: { email: inv.email || null, role: inv.role } });
      setInv({ email: "", role: "candidate" });
      load();
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function updateMember(id: string, patch: Partial<Member>) {
    try {
      await api(`/org/members/${id}`, { method: "PATCH", body: patch });
      load();
    } catch (e: any) {
      setError(e.message);
    }
  }

  return (
    <div className="stack">
      <h2>{user?.tenant.name} <span className="pill">{user?.tenant.plan} plan</span></h2>
      <div className="stats">
        {Object.entries(usage).map(([k, u]) => (
          <div key={k} className="card stat">
            <div className="muted small">{k === "llm" ? "AI requests" : "Code runs"} ({u.window_days}d)</div>
            <div className="stat-value">{u.used} / {u.limit ?? "∞"}</div>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>Cohort leaderboard</h3>
        <table className="table">
          <thead><tr><th>Member</th><th>Role</th><th>Solved</th><th>Submissions</th><th>Interviews</th><th>Avg score</th></tr></thead>
          <tbody>
            {board.map((r) => (
              <tr key={r.user_id}>
                <td>{r.full_name}</td><td>{r.role}</td><td>{r.problems_solved}</td><td>{r.submissions}</td>
                <td>{r.interviews_completed}</td><td>{r.avg_interview_score ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {admin && (
        <>
          <div className="card stack">
            <h3>Invite members</h3>
            <div className="row">
              <input placeholder="Email (optional - locks invite to this address)" value={inv.email} onChange={(e) => setInv({ ...inv, email: e.target.value })} />
              <select value={inv.role} onChange={(e) => setInv({ ...inv, role: e.target.value })}>
                <option value="candidate">candidate</option>
                <option value="mentor">mentor</option>
                {user?.role === "owner" && <option value="admin">admin</option>}
              </select>
              <button className="primary" onClick={invite}>Create invite</button>
            </div>
            {error && <div className="error">{error}</div>}
            <table className="table small">
              <thead><tr><th>Code (share this)</th><th>Role</th><th>Email</th><th>Status</th></tr></thead>
              <tbody>
                {invites.map((i) => (
                  <tr key={i.id}>
                    <td><code>{i.code}</code></td><td>{i.role}</td><td>{i.email ?? "any"}</td>
                    <td>{i.used_at ? "used" : new Date(i.expires_at) < new Date() ? "expired" : "open"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <h3>Members</h3>
            <table className="table">
              <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Active</th></tr></thead>
              <tbody>
                {members.map((m) => (
                  <tr key={m.id}>
                    <td>{m.full_name}</td><td>{m.email}</td>
                    <td>
                      {m.role === "owner" || m.id === user?.id ? m.role : (
                        <select value={m.role} onChange={(e) => updateMember(m.id, { role: e.target.value })}>
                          <option>candidate</option><option>mentor</option>
                          {user?.role === "owner" && <option>admin</option>}
                        </select>
                      )}
                    </td>
                    <td>
                      {m.role !== "owner" && m.id !== user?.id
                        ? <input type="checkbox" checked={m.is_active} onChange={(e) => updateMember(m.id, { is_active: e.target.checked })} />
                        : "✓"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
