export type PublicConfig = {
  supabase_url: string;
  supabase_publishable_key: string;
  auth_required: boolean;
};

export type MeResponse = {
  user: { id: string; email: string | null };
  workspace: { id: string; role: string };
  memberships: Array<{
    workspace_id: string;
    user_id: string;
    role: string;
    workspace_name?: string;
    created_at?: string | null;
  }>;
};

export type BootstrapResponse = {
  workspace: { id: string; name: string; created_at?: string | null };
  membership: { workspace_id: string; user_id: string; role: string };
  created: boolean;
};

/** Backend-authoritative connection status (see sanitize_store_view / connection_health). */
export type ConnectionStatus = "connected" | "needs_reconnection" | "connection_error";

export type StoreView = {
  connected: boolean;
  /** Internal UUID primary key when available — prefer for future metrics joins. */
  id?: string | null;
  store_id: string;
  display_name: string;
  /** Resolved label (may match display_name). */
  store_name: string;
  /** Daraz shop identity when distinct from email. */
  shop_name?: string;
  account: string;
  seller_id: string;
  country: string;
  access_token_expires_at?: string | null;
  access_token_expires_in_seconds?: number | null;
  refresh_token_expires_in_seconds?: number | null;
  connection_status?: ConnectionStatus;
  /** null = expiry unknown — do not invent Healthy counts */
  needs_attention?: boolean | null;
  authorized_at?: string | null;
  updated_at?: string | null;
};

export type StoreGroup = {
  id: string;
  name: string;
  store_ids: string[];
  created_at?: string | null;
  updated_at?: string | null;
};

export type OrderRow = {
  order_id: string | number;
  order_number?: string | number;
  items_count?: number;
  statuses?: string | string[];
  created_at?: string;
  store_id?: string;
  store_name?: string;
  display_name?: string;
};

export type LabelDetail = {
  order_id: string | number;
  store_name?: string;
  mime_type?: string;
  fetch_source?: string;
  converted?: boolean;
  display?: string;
  kind?: string;
};

export type PrintJobStatus = {
  id?: string;
  job_id?: string;
  status: "idle" | "processing" | "done" | "error" | string;
  message?: string;
  error?: string | null;
  pages?: number;
  labels?: number;
  download_url?: string;
  label_details?: LabelDetail[];
  label_summary?: { pdf_native?: number; html_converted?: number };
  started_at?: string | null;
  updated_at?: string | null;
  result?: Record<string, unknown> | null;
};

export type PrintJobListItem = {
  id: string;
  status: string;
  message?: string;
  error?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  pages?: number | null;
  labels?: number | null;
  has_download: boolean;
};

/** Leaderboard-safe performance row (no tokens / workspace ops metadata). */
export type PerformanceRow = {
  store_id: string;
  display_name: string;
  rank: number;
  orders_count: number;
  gross_sales: number | null;
  currency: string;
  orders_growth_pct: number | null;
  gross_sales_growth_pct: number | null;
  year: number;
  month: number;
  sync_status?: string | null;
  orders_synced_at?: string | null;
  gross_sales_synced_at?: string | null;
  updated_at?: string | null;
};

export type PerformanceLeaderboardResponse = {
  year: number;
  month: number;
  metric: "orders" | "gross_sales" | string;
  timezone: string;
  gross_sales_enabled: boolean;
  last_synced_at: string | null;
  leaderboard: PerformanceRow[];
  workspace_id: string;
};

export type PerformanceMonthsResponse = {
  months: Array<{ year: number; month: number }>;
  current: { year: number; month: number };
  timezone: string;
};

export type PerformanceSyncResponse = {
  year: number;
  month: number;
  stores_total: number;
  stores_ok: number;
  stores_partial: number;
  stores_error: number;
  leaderboard: PerformanceRow[];
  last_synced_at: string | null;
  gross_sales_enabled?: boolean;
};
