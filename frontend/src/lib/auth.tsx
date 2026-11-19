import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, logout, tokens, type Tokens, type User } from "./api";

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

  const loadMe = useCallback(async () => {
    if (!tokens.get()) return setLoading(false);
    try {
      setUser(await api<User>("/auth/me"));
    } catch {
      tokens.clear();
    } finally {
      setLoading(false);
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

  return <Ctx.Provider value={{ user, loading, signIn, signOut }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);

export const isStaff = (u: User | null) => !!u && ["owner", "admin", "mentor"].includes(u.role);
export const isAdmin = (u: User | null) => !!u && ["owner", "admin"].includes(u.role);
