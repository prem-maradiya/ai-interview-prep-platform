import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { ApiError, api, logout, tokens, type Tokens, type User } from "./api";

type AuthCtx = {
  user: User | null;
  loading: boolean;
  signIn: (path: string, body: unknown) => Promise<void>;
  signOut: () => void;
};

const Ctx = createContext<AuthCtx>(null!);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const [unreachable, setUnreachable] = useState(false);

  const loadMe = useCallback(async (attempt = 0): Promise<void> => {
    if (!tokens.get()) return setLoading(false);
    try {
      setUser(await api<User>("/auth/me"));
      setUnreachable(false);
      setLoading(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        tokens.clear(); // session really is invalid (refresh already failed)
        setLoading(false);
        return;
      }
      // Server restarting / network blip: keep the session and retry with backoff.
      setUnreachable(true);
      setTimeout(() => loadMe(attempt + 1), Math.min(10_000, 1000 * 2 ** attempt));
    }
  }, []);

  useEffect(() => {
    loadMe();
  }, [loadMe]);

  const signIn = async (path: string, body: unknown) => {
    tokens.set(await api<Tokens>(path, { body }));
    setUser(await api<User>("/auth/me"));
  };

  const signOut = () => {
    logout(); // revokes the refresh token server-side
    setUser(null);
  };

  if (loading && unreachable) {
    return <div className="center muted">Can't reach the server - retrying…</div>;
  }
  return <Ctx.Provider value={{ user, loading, signIn, signOut }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);

export const isStaff = (u: User | null) => !!u && ["owner", "admin", "mentor"].includes(u.role);
export const isAdmin = (u: User | null) => !!u && ["owner", "admin"].includes(u.role);
