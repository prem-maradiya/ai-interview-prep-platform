import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

/** Landing page after social sign-in. The server puts our tokens (or an error) in the URL fragment,
 *  which browsers never send to servers. We read it, then wipe it from the address bar/history. */
export default function OAuthComplete() {
  const { completeSignIn } = useAuth();
  const nav = useNavigate();
  const [error, setError] = useState("");
  const [working, setWorking] = useState(true);
  const handled = useRef(""); // fragment already processed (StrictMode runs effects twice)

  useEffect(() => {
    const process = () => {
      const raw = window.location.hash.slice(1);
      if (!raw || raw === handled.current) return;
      handled.current = raw;
      window.history.replaceState(null, "", "/oauth/complete"); // don't leave tokens in history
      const frag = new URLSearchParams(raw);
      if (frag.get("error")) {
        setError(frag.get("error")!);
        setWorking(false);
        return;
      }
      const access_token = frag.get("access_token"), refresh_token = frag.get("refresh_token");
      if (!access_token || !refresh_token) {
        setError("Sign-in didn't complete. Please try again.");
        setWorking(false);
        return;
      }
      setError("");
      setWorking(true);
      const linked = frag.get("linked");
      completeSignIn({ access_token, refresh_token })
        .then(() => nav(linked ? `/account?linked=${linked}` : "/", { replace: true }))
        .catch(() => { setError("Sign-in didn't complete. Please try again."); setWorking(false); });
    };
    process();
    // A fragment-only change doesn't reload the page, so listen for it too.
    window.addEventListener("hashchange", process);
    return () => window.removeEventListener("hashchange", process);
  }, []);

  return (
    <div className="auth">
      <div className="card auth-card stack">
        <h1 className="brand big">PrepForge</h1>
        {error ? (
          <>
            <div className="error">{error}</div>
            <a href="/" className="button-link">Back to sign in</a>
          </>
        ) : (
          <p className="muted">{working ? "Signing you in…" : ""}</p>
        )}
      </div>
    </div>
  );
}
