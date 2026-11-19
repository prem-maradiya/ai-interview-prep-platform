import { useState, type FormEvent } from "react";
import { useAuth } from "../lib/auth";
import { SocialButton, startSocial, useProviders } from "../components/SocialButtons";

type Mode = "login" | "register" | "invite";
const SLUG = /^[a-z0-9-]{2,60}$/;

export default function AuthPage() {
  const { signIn } = useAuth();
  const providers = useProviders();
  const params = new URLSearchParams(window.location.search);
  const [mode, setMode] = useState<Mode>(params.get("invite") ? "invite" : "login");
  const [f, setF] = useState({
    org_slug: params.get("org") ?? "", org_name: "", full_name: "", email: "", password: "", code: params.get("invite") ?? "",
  });
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

  // What social sign-in needs before it can start, per tab.
  const socialReady =
    mode === "login" ? SLUG.test(f.org_slug)
      : mode === "register" ? SLUG.test(f.org_slug) && f.org_name.trim().length >= 2
        : f.code.trim().length > 0;
  const socialParams: Record<string, string> =
    mode === "login" ? { mode: "login", org_slug: f.org_slug }
      : mode === "register" ? { mode: "signup", org_slug: f.org_slug, org_name: f.org_name.trim() }
        : { mode: "invite", invite: f.code.trim() };
  const hint = mode === "invite" ? "Enter your invite code to continue" : "Enter your organization first";

  return (
    <div className="auth">
      <div className="card auth-card">
        <h1 className="brand big">PrepForge</h1>
        <p className="muted">AI interview practice for your whole organization.</p>
        <div className="tabs">
          {(["login", "register", "invite"] as Mode[]).map((m) => (
            <button key={m} className={mode === m ? "active" : ""} onClick={() => { setMode(m); setError(""); }} type="button">
              {{ login: "Sign in", register: "New organization", invite: "Join with invite" }[m]}
            </button>
          ))}
        </div>
        <form onSubmit={submit} className="stack">
          {mode === "register" && <input placeholder="Organization name" value={f.org_name} onChange={set("org_name")} required />}
          {mode !== "invite" && (
            <input placeholder="Organization short name (e.g. acme-bootcamp)" value={f.org_slug} pattern="[a-z0-9\-]+"
              onChange={set("org_slug")} required autoCapitalize="none" />
          )}
          {mode === "invite" && <input placeholder="Invite code" value={f.code} onChange={set("code")} required />}

          {providers.length > 0 && (
            <>
              <div className="stack tight">
                {providers.map((p) => (
                  <SocialButton key={p.name} p={p} disabled={!socialReady} onClick={() => startSocial(p.name, socialParams)} />
                ))}
                {!socialReady && <div className="small muted">{hint}</div>}
              </div>
              <div className="divider"><span>or with email</span></div>
            </>
          )}

          {mode !== "login" && <input placeholder="Full name" value={f.full_name} onChange={set("full_name")} required />}
          <input type="email" placeholder="Email" value={f.email} onChange={set("email")} required autoComplete="email" />
          <input type="password" placeholder="Password (8+ chars)" value={f.password} minLength={8} onChange={set("password")} required
            autoComplete={mode === "login" ? "current-password" : "new-password"} />
          {error && <div className="error">{error}</div>}
          <button className="primary" disabled={busy}>{busy ? "…" : { login: "Sign in", register: "Create organization", invite: "Join" }[mode]}</button>
        </form>
      </div>
    </div>
  );
}
