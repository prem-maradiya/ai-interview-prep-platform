import { useEffect, useState } from "react";
import { api, apiDownload } from "../lib/api";

type Event = {
  id: string; action: string; description: string; actor_name: string; actor_email: string;
  target_label: string; details: Record<string, unknown>; ip: string | null; created_at: string;
};

/** Who did what in the organization: role changes, deactivations, invites, problems, assignments, sign-in changes. */
export default function AuditLog() {
  const [items, setItems] = useState<Event[] | null>(null);
  const [actions, setActions] = useState<Record<string, string>>({});
  const [action, setAction] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<{ actions: Record<string, string>; items: Event[] }>(`/org/audit?limit=100${action ? `&action=${action}` : ""}`)
      .then((r) => { setActions(r.actions); setItems(r.items); })
      .catch((e) => setError(e.message));
  }, [action]);

  const details = (d: Record<string, unknown>) =>
    Object.entries(d).map(([k, v]) => (k === "from" || k === "to" ? null : `${k}: ${v}`)).filter(Boolean).join(", ");

  return (
    <div className="card stack">
      <div className="row between wrap">
        <h3>Audit log</h3>
        <div className="row">
          <select value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">All actions</option>
            {Object.entries(actions).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <button onClick={() => apiDownload(`/org/audit.csv${action ? `?action=${action}` : ""}`, "audit-log.csv")}>Export CSV</button>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {items === null ? <div className="muted small">Loading…</div> : items.length === 0 ? (
        <div className="muted small">Nothing recorded yet.</div>
      ) : (
        <table className="table">
          <thead><tr><th>When</th><th>Who</th><th>What</th><th>Target</th></tr></thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.id}>
                <td className="small">{new Date(e.created_at).toLocaleString()}</td>
                <td>{e.actor_name || <span className="muted">system</span>}</td>
                <td>
                  {e.description}
                  {"from" in e.details && <span className="small muted"> ({String(e.details.from)} → {String(e.details.to)})</span>}
                  {details(e.details) && <div className="small muted">{details(e.details)}</div>}
                </td>
                <td className="small">{e.target_label}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
