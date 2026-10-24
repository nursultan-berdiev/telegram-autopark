import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import type { WhoAmI } from "./types";

interface AuthCtx {
  me: WhoAmI | null;
  loading: boolean;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthCtx>({ me: null, loading: true, logout: async () => {} });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<WhoAmI | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<WhoAmI>("/whoami")
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setLoading(false));
  }, []);

  const logout = async () => {
    try {
      await api.post("/web-logout");
    } catch {
      // даже если запрос не прошёл — считаем себя вышедшими локально
    }
    setMe(null);
  };

  return <Ctx.Provider value={{ me, loading, logout }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
