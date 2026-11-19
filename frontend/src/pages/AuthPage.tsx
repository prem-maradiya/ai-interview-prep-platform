import { useState, type FormEvent } from "react";
import { useAuth } from "../lib/auth";

type Mode = "login" | "register" | "invite";

export default function AuthPage() {
  const { signIn } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [f, setF] = useState({ org_slug: "", org_name: "", full_name: "", email: "", password: "", code: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (mode === "login") await signIn("/auth/login", { org_slug: f.org_slug, email: f.email, password: f.password });
      if (mode === "register") await signIn("/auth/register-org", { org_name: f.org_name, org_slug: f.org_slug, full_name: f.full_name, email: f.email, password: f.password });
      if (mode === "invite") await signIn("/auth/accept-invite", { code: f.code, full_name: f.full_name, email: f.email, password: f.password });
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <div className="card auth-card">
        <h1 className="brand big">PrepForge</h1>
        <p className="muted">AI interview practice for your whole organization.</p>
        <div className="tabs">
          {(["login", "register", "invite"] as Mode[]).map((m) => (
            <button key={m} className={mode === m ? "active" : ""} onClick={() => setMode(m)} type="button">
              {{ login: "Sign in", register: "New organization", invite: "Join with invite" }[m]}
            </button>
          ))}
        </div>
        <form onSubmit={submit} className="stack">
          {mode === "register" && <input placeholder="Organization name" value={f.org_name} onChange={set("org_name")} required />}
          {mode !== "invite" && (
            <input placeholder="Organization slug (e.g. acme-bootcamp)" value={f.org_slug} pattern="[a-z0-9\-]+" onChange={set("org_slug")} required />
          )}
          {mode === "invite" && <input placeholder="Invite code" value={f.code} onChange={set("code")} required />}
          {mode !== "login" && <input placeholder="Full name" value={f.full_name} onChange={set("full_name")} required />}
          <input type="email" placeholder="Email" value={f.email} onChange={set("email")} required />
          <input type="password" placeholder="Password (8+ chars)" value={f.password} minLength={8} onChange={set("password")} required />
          {error && <div className="error">{error}</div>}
          <button className="primary" disabled={busy}>{busy ? "…" : { login: "Sign in", register: "Create organization", invite: "Join" }[mode]}</button>
        </form>
      </div>
    </div>
  );
}
