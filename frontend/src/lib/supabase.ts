import { createClient, type SupabaseClient, type Session } from "@supabase/supabase-js";
import type { PublicConfig } from "@/types/api";

const WORKSPACE_KEY = "multistore_workspace_id";

let client: SupabaseClient | null = null;
let publicConfig: PublicConfig | null = null;

export async function loadPublicConfig(): Promise<PublicConfig> {
  if (publicConfig) return publicConfig;
  const res = await fetch("/api/public-config");
  if (!res.ok) {
    throw new Error("Could not load public configuration");
  }
  publicConfig = (await res.json()) as PublicConfig;
  return publicConfig;
}

export async function getSupabase(): Promise<SupabaseClient> {
  if (client) return client;
  const cfg = await loadPublicConfig();
  if (!cfg.supabase_url || !cfg.supabase_publishable_key) {
    throw new Error("Supabase is not configured on the server");
  }
  client = createClient(cfg.supabase_url, cfg.supabase_publishable_key, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
      storageKey: "multistore_supabase_auth",
    },
  });
  return client;
}

export async function getSession(): Promise<Session | null> {
  const sb = await getSupabase();
  const { data, error } = await sb.auth.getSession();
  if (error) throw error;
  return data.session;
}

export async function getAccessToken(): Promise<string | null> {
  const session = await getSession();
  return session?.access_token ?? null;
}

export async function signIn(email: string, password: string) {
  const sb = await getSupabase();
  const { data, error } = await sb.auth.signInWithPassword({ email, password });
  if (error) throw error;
  return data;
}

export async function signUp(email: string, password: string) {
  const sb = await getSupabase();
  const { data, error } = await sb.auth.signUp({ email, password });
  if (error) throw error;
  if (!data.session) {
    throw new Error(
      "Check your email to confirm the account, then sign in. " +
        "(Or disable email confirmations in Supabase Auth for local testing.)"
    );
  }
  return data;
}

export async function signOut() {
  const sb = await getSupabase();
  await sb.auth.signOut();
  localStorage.removeItem(WORKSPACE_KEY);
}

export function getWorkspaceId(): string {
  return localStorage.getItem(WORKSPACE_KEY) || "";
}

export function setWorkspaceId(id: string) {
  if (id) localStorage.setItem(WORKSPACE_KEY, id);
}

export { WORKSPACE_KEY };
