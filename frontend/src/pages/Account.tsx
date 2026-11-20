import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { SocialButton, startSocial, useProviders } from "../components/SocialButtons";
import EmailPreferences from "../components/EmailPreferences";

type Identities = {
  has_password: boolean;
  identities: { id: string; provider: string; email: string | null; created_at: string; last_login_at: string | null }[];
};

export default function Account() {
  const { user } = useAuth();
  const providers = useProviders();
  const [params] = useSearchParams();
  const [data, setData] = useState<Identities | null>(null);
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [msg, setMsg] = useState(params.get("linked") ? `${params.get("linked")} sign-in linked.` : "");
  const [error, setError] = useState("");

  const load = () => api<Identities>("/auth/identities").then(setData).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  async function link(provider: string) {
    setError("");
    try {
      const { ticket } = await api<{ ticket: string }>("/auth/oauth/link-ticket", { method: "POST" });
      startSocial(provider, { mode: "link", ticket });
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function unlink(id: string, provider: string) {
    if (!confirm(`Unlink ${provider}? You won't be able to sign in with it until you link it again.`)) return;
    setError("");
    try {
      await api(`/auth/identities/${id}`, { method: "DELETE" });
      setMsg(`${provider} unlinked.`);
      load();
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function savePassword() {
    setError("");
    try {
      await api("/auth/password", {
        body: { new_password: pw.new_password, current_password: data?.has_password ? pw.current_password : null },
      });
      setPw({ current_password: "", new_password: "" });
      setMsg(data?.has_password ? "Password changed." : "Password set. You can now also sign in with email and password.");
      load();
    } catch (e: any) {
      setError(e.message);
    }
  }

  if (!data) return <div className="muted">{error || "Loading…"}</div>;
  const linked = new Set(data.identities.map((i) => i.provider));

  return (
    <div className="stack account">
      <h2>Account</h2>
      <div className="card">
        <b>{user?.full_name}</b> · {user?.email} <span className="pill">{user?.role}</span>
        <div className="small muted">Organization: {user?.tenant.name} ({user?.tenant.slug})</div>
      </div>
      {msg && <div className="card ok-note">{msg}</div>}
      {error && <div className="error">{error}</div>}

      <div className="card stack">
        <h3>Sign-in methods</h3>
        <div className="row between task-row">
          <span>✉️ Email &amp; password</span>
          <span className="small muted">{data.has_password ? "Set" : "Not set"}</span>
        </div>
        {data.identities.map((i) => (
          <div key={i.id} className="row between task-row">
            <span><b>{providers.find((p) => p.name === i.provider)?.label ?? i.provider}</b> {i.email && <span className="small muted">({i.email})</span>}</span>
            <span className="row">
              <span className="small muted">{i.last_login_at ? `last used ${new Date(i.last_login_at).toLocaleDateString()}` : ""}</span>
              <button className="link" onClick={() => unlink(i.id, i.provider)}>Unlink</button>
            </span>
          </div>
        ))}
        {providers.filter((p) => !linked.has(p.name)).length > 0 && (
          <div className="stack tight">
            {providers.filter((p) => !linked.has(p.name)).map((p) => (
              <SocialButton key={p.name} p={p} verb="Link" onClick={() => link(p.name)} />
            ))}
          </div>
        )}
        {providers.length === 0 && <div className="small muted">Social sign-in isn't enabled on this server.</div>}
      </div>

      <div className="card stack">
        <h3>{data.has_password ? "Change password" : "Set a password"}</h3>
        {!data.has_password && <div className="small muted">Add a password so you can sign in without a social account.</div>}
        {data.has_password && (
          <input type="password" placeholder="Current password" value={pw.current_password} autoComplete="current-password"
            onChange={(e) => setPw({ ...pw, current_password: e.target.value })} />
        )}
        <input type="password" placeholder="New password (8+ chars)" value={pw.new_password} autoComplete="new-password"
          onChange={(e) => setPw({ ...pw, new_password: e.target.value })} />
        <div><button className="primary" disabled={pw.new_password.length < 8} onClick={savePassword}>Save password</button></div>
      </div>

      <EmailPreferences />
    </div>
  );
}
