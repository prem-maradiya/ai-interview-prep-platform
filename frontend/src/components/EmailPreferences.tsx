import { useEffect, useState } from "react";
import { api } from "../lib/api";

type Prefs = { email: string; delivery_enabled: boolean; options: { kind: string; label: string; enabled: boolean }[] };

/** Per-kind email opt-ins. Each toggle saves immediately. */
export default function EmailPreferences() {
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    api<Prefs>("/notifications/email-preferences").then(setPrefs).catch((e) => setError(e.message));
  }, []);

  async function toggle(kind: string, enabled: boolean) {
    setSaving(kind);
    setError("");
    try {
      setPrefs(await api<Prefs>("/notifications/email-preferences", { method: "PUT", body: { [kind]: enabled } }));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="card stack">
      <h3>Email notifications</h3>
      {!prefs ? (
        <div className="small muted">{error || "Loading…"}</div>
      ) : (
        <>
          <div className="small muted">
            Sent to <b>{prefs.email}</b>. Password resets and invites are always sent.
            {!prefs.delivery_enabled && " Email delivery is turned off on this server."}
          </div>
          {prefs.options.map((o) => (
            <label key={o.kind} className="row between task-row toggle-row">
              <span>{o.label}</span>
              <input type="checkbox" checked={o.enabled} disabled={saving === o.kind}
                onChange={(e) => toggle(o.kind, e.target.checked)} />
            </label>
          ))}
          {error && <div className="error">{error}</div>}
        </>
      )}
    </div>
  );
}
