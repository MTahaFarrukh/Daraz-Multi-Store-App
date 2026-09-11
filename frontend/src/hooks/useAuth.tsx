import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import {
  getSession,
  getSupabase,
  setWorkspaceId,
  signIn as sbSignIn,
  signOut as sbSignOut,
  signUp as sbSignUp,
} from "@/lib/supabase";
import type { MeResponse } from "@/types/api";

type AuthState = {
  loading: boolean;
  authenticated: boolean;
  me: MeResponse | null;
  error: string | null;
  refresh: () => Promise<void>;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

async function loadWorkspace(): Promise<MeResponse> {
  await Api.bootstrap();
  const me = await Api.me();
  if (me.workspace?.id) setWorkspaceId(me.workspace.id);
  return me;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    const session = await getSession();
    if (!session) {
      setAuthenticated(false);
      setMe(null);
      return;
    }
    setAuthenticated(true);
    const profile = await loadWorkspace();
    setMe(profile);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await getSupabase();
        if (!cancelled) await refresh();
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Auth failed");
          setAuthenticated(false);
          setMe(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    let unsubscribe: (() => void) | undefined;
    getSupabase()
      .then((sb) => {
        const { data } = sb.auth.onAuthStateChange(async (event) => {
          if (event === "SIGNED_OUT") {
            setAuthenticated(false);
            setMe(null);
          }
          if (event === "TOKEN_REFRESHED" || event === "SIGNED_IN") {
            try {
              await refresh();
            } catch {
              /* ignore transient */
            }
          }
        });
        unsubscribe = () => data.subscription.unsubscribe();
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, [refresh]);

  const value = useMemo<AuthState>(
    () => ({
      loading,
      authenticated,
      me,
      error,
      refresh,
      signIn: async (email, password) => {
        await sbSignIn(email, password);
        setLoading(true);
        try {
          await refresh();
          setAuthenticated(true);
        } finally {
          setLoading(false);
        }
      },
      signUp: async (email, password) => {
        await sbSignUp(email, password);
        setLoading(true);
        try {
          await refresh();
          setAuthenticated(true);
        } finally {
          setLoading(false);
        }
      },
      signOut: async () => {
        await sbSignOut();
        setAuthenticated(false);
        setMe(null);
      },
    }),
    [loading, authenticated, me, error, refresh]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function isUnauthorized(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401;
}
