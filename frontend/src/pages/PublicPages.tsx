import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";

/** Pages reachable from emails, signed in or not: password reset and unsubscribe. */
function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="auth">
      <div className="card auth-card stack">
        <h1 className="brand big">PrepForge</h1>
        <h3>{title}</h3>
        {children}
      </div>
    </div>
  );
}

export function ForgotPassword() {
  const [params] = useSearchParams();
  const [f, setF] = useState({ org_slug: params.get("org") ?? "", email: "" });
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/auth/password-reset/request", { body: { org_slug: f.org_slug.trim(), email: f.email.trim() } });
      setSent(true);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Reset your password">
      {sent ? (
        <div className="ok-note card">
          If an account for <b>{f.email}</b> exists in <b>{f.org_slug}</b>, we've emailed a link to choose a new password.
          It expires in 30 minutes.
        </div>
      ) : (
        <form className="stack" onSubmit={submit}>
          <p className="small muted">Enter your organization and email, and we'll send you a reset link.</p>
          <input placeholder="Organization short name" value={f.org_slug} pattern="[a-z0-9\-]+" required autoCapitalize="none"
            onChange={(e) => setF({ ...f, org_slug: e.target.value })} />
          <input type="email" placeholder="Email" value={f.email} required autoComplete="email"
            onChange={(e) => setF({ ...f, email: e.target.value })} />
          {error && <div className="error">{error}</div>}
          <button className="primary" disabled={busy}>{busy ? "…" : "Email me a reset link"}</button>
        </form>
      )}
      <Link to="/" className="small">Back to sign in</Link>
    </AuthCard>
  );
}

export function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [pw, setPw] = useState({ a: "", b: "" });
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (pw.a !== pw.b) return setError("The passwords don't match.");
    setBusy(true);
    setError("");
    try {
      await api("/auth/password-reset/confirm", { body: { token, password: pw.a } });
      setDone(true);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Choose a new password">
      {done ? (
        <div className="ok-note card">Password changed. You've been signed out everywhere else - sign in with your new password.</div>
      ) : !token ? (
        <div className="error">This link is missing its reset code. Open the link from the email again.</div>
      ) : (
        <form className="stack" onSubmit={submit}>
          <input type="password" placeholder="New password (8+ chars)" minLength={8} required autoComplete="new-password"
            value={pw.a} onChange={(e) => setPw({ ...pw, a: e.target.value })} />
          <input type="password" placeholder="Repeat new password" minLength={8} required autoComplete="new-password"
            value={pw.b} onChange={(e) => setPw({ ...pw, b: e.target.value })} />
          {error && <div className="error">{error}</div>}
          <button className="primary" disabled={busy}>{busy ? "…" : "Save new password"}</button>
        </form>
      )}
      <Link to="/" className="small">{done ? "Sign in" : "Back to sign in"}</Link>
    </AuthCard>
  );
}

export function Unsubscribe() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [state, setState] = useState<{ email?: string; error?: string; busy?: boolean }>({});

  async function confirm() {
    setState({ busy: true });
    try {
      const r = await api<{ email: string }>("/notifications/unsubscribe", { body: { token } });
      setState({ email: r.email });
    } catch (err: any) {
      setState({ error: err.message });
    }
  }

  return (
    <AuthCard title="Email notifications">
      {state.email ? (
        <div className="ok-note card">
          Done - <b>{state.email}</b> won't get notification or digest emails any more. Password resets and invites still arrive.
          You can turn emails back on in Account settings.
        </div>
      ) : (
        <>
          <p className="muted">Stop all notification and weekly digest emails from PrepForge?</p>
          {state.error && <div className="error">{state.error}</div>}
          <div><button className="primary" disabled={!token || state.busy} onClick={confirm}>Unsubscribe</button></div>
        </>
      )}
      <Link to="/" className="small">Open PrepForge</Link>
    </AuthCard>
  );
}
