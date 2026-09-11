import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import { clearAuthenticatedCache } from "@/lib/queryKeys";
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
  const queryClient = useQueryClient();
  const [loading, setLoading] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const wipeServerCache = useCallback(() => {
    clearAuthenticatedCache(queryClient);
  }, [queryClient]);

  const refresh = useCallback(async () => {
    setError(null);
    const session = await getSession();
    if (!session) {
      setAuthenticated(false);
      setMe(null);
      wipeServerCache();
      return;
    }
    setAuthenticated(true);
    const profile = await loadWorkspace();
    setMe((prev) => {
      const prevUser = prev?.user?.id;
      const prevWs = prev?.workspace?.id;
      const nextUser = profile.user?.id;
      const nextWs = profile.workspace?.id;
      if (prev && (prevUser !== nextUser || prevWs !== nextWs)) {
        wipeServerCache();
      }
      return profile;
    });
  }, [wipeServerCache]);

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
          wipeServerCache();
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
            wipeServerCache();
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
  }, [refresh, wipeServerCache]);

  const value = useMemo<AuthState>(
    () => ({
      loading,
      authenticated,
      me,
      error,
      refresh,
      signIn: async (email, password) => {
        wipeServerCache();
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
        wipeServerCache();
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
        wipeServerCache();
      },
    }),
    [loading, authenticated, me, error, refresh, wipeServerCache]
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
