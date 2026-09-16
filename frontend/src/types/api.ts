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

export type OrderStatusGroup =
  | "pending"
  | "ready_to_ship"
  | "shipped"
  | "delivered"
  | "canceled"
  | "returned"
  | "other"
  | string;

export type PrintStateFilter = "unprinted" | "printed" | "any";

/** Local DB order row from GET /api/orders (Unified Orders). */
export type UnifiedOrder = {
  id: string;
  store_id: string;
  store_slug?: string | null;
  store_display_name?: string | null;
  daraz_order_id: string;
  order_number?: string | number | null;
  status_raw?: string | null;
  status_group?: OrderStatusGroup | null;
  statuses?: string | string[] | null;
  price?: number | string | null;
  currency?: string | null;
  items_count?: number | null;
  customer_first_name?: string | null;
  customer_last_name?: string | null;
  payment_method?: string | null;
  shipping_fee?: number | string | null;
  warehouse_code?: string | null;
  synced_at?: string | null;
  has_print?: boolean | null;
  print_count?: number | null;
  last_printed_at?: string | null;
  created_at_daraz?: string | null;
  updated_at_daraz?: string | null;
};

export type OrderListParams = {
  stores?: string;
  store_ids?: string;
  group_id?: string;
  status_group?: string;
  status?: string;
  search?: string;
  date_from?: string;
  date_to?: string;
  print_state?: PrintStateFilter;
  page?: number;
  page_size?: number;
  sort?: string;
};

export type OrderListResponse = {
  orders: UnifiedOrder[];
  items: UnifiedOrder[];
  count: number;
  total: number;
  page: number;
  page_size: number;
};

export type OrderStatusCountsResponse = {
  counts: Record<string, number>;
};

export type OrderItemRow = {
  id?: string;
  daraz_order_item_id?: string | null;
  daraz_order_id?: string | null;
  status_raw?: string | null;
  package_id?: string | null;
  name?: string | null;
  sku?: string | null;
  quantity?: number | null;
  item_price?: number | string | null;
  paid_price?: number | string | null;
  currency?: string | null;
  tracking_code?: string | null;
  shipment_provider?: string | null;
  shipping_type?: string | null;
  warehouse_code?: string | null;
};

export type OrderPrintHistoryRow = {
  id?: string;
  printed_at?: string | null;
  is_reprint?: boolean | null;
  package_id?: string | null;
  order_item_ids?: string[] | null;
  fetch_source?: string | null;
  print_job_id?: string | null;
};

export type OrderDetailResponse = {
  order: UnifiedOrder;
  items: OrderItemRow[];
  print_history: OrderPrintHistoryRow[];
  print_summary?: {
    has_print?: boolean;
    print_count?: number;
    last_printed_at?: string | null;
  };
};

export type OrderSyncResponse = {
  stores: number;
  ok: number;
  failed: number;
  results: Array<{
    store_id?: string;
    sync_status?: string;
    sync_error?: string | null;
    orders_upserted?: number;
    items_upserted?: number;
    warning?: string | null;
  }>;
};

export type PrintTargetEntry = {
  order_id: string;
  store_id?: string;
  daraz_order_id?: string;
  status_group?: string | null;
  order_item_ids?: string[];
  package_id?: string | null;
  reason?: string;
  error?: string;
};

export type PrintValidateResponse = {
  new_printable: PrintTargetEntry[];
  already_printed: PrintTargetEntry[];
  not_eligible: PrintTargetEntry[];
  errors: PrintTargetEntry[];
};

export type PrintOrdersStartResponse = {
  status: string;
  job_id: string;
  poll_url: string;
  download_url: string;
  message?: string;
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

export type ProductListParams = {
  stores?: string;
  store_ids?: string;
  group_id?: string;
  status?: string;
  category_id?: number | string;
  search?: string;
  page?: number;
  page_size?: number;
  sort?: string;
};

export type ProductRow = {
  id: string;
  store_id: string;
  store_slug?: string | null;
  store_display_name?: string | null;
  daraz_item_id: string;
  title?: string | null;
  title_en?: string | null;
  primary_category_id?: number | null;
  primary_category_name?: string | null;
  brand?: string | null;
  status_raw?: string | null;
  product_url?: string | null;
  images_json?: Array<{ url?: string; position?: number; kind?: string }>;
  video_ref?: string | null;
  has_video?: boolean;
  primary_image?: string | null;
  variant_count?: number | null;
  variants_count?: number | null;
  price_min?: number | null;
  price_max?: number | null;
  stock_total?: number | null;
  synced_at?: string | null;
  package_content?: string | null;
  description?: string | null;
  description_en?: string | null;
  attributes_json?: Record<string, unknown>;
  variation_json?: Record<string, unknown>;
};

export type ProductVariant = {
  id: string;
  daraz_sku_id: string;
  seller_sku?: string | null;
  shop_sku?: string | null;
  sale_props_json?: Record<string, string>;
  price?: number | null;
  special_price?: number | null;
  quantity?: number | null;
  package_weight?: number | null;
  package_length?: number | null;
  package_width?: number | null;
  package_height?: number | null;
  images_json?: Array<{ url?: string }>;
  status_raw?: string | null;
};

export type ProductListResponse = {
  products: ProductRow[];
  items: ProductRow[];
  total: number;
  page: number;
  page_size: number;
};

export type ProductDetailResponse = {
  product: ProductRow;
  variants: ProductVariant[];
};

export type ProductDefaults = {
  workspace_id: string;
  default_package_weight?: number | null;
  default_package_length?: number | null;
  default_package_width?: number | null;
  default_package_height?: number | null;
  default_initial_quantity?: number | null;
  sku_prefix?: string | null;
  updated_at?: string | null;
};

export type ProductSyncResponse = {
  stores: number;
  ok: number;
  failed: number;
  results: Array<{
    store_id?: string;
    sync_status?: string;
    sync_error?: string | null;
    products_upserted?: number;
    variants_upserted?: number;
  }>;
};

export type ProductCloneDraftResponse = {
  draft: Record<string, unknown>;
  fidelity: {
    copied?: string[];
    changed_by_multistore?: string[];
    not_available?: string[];
  };
  warnings: string[];
  errors: string[];
  possible_duplicates: Array<{
    id?: string;
    title?: string;
    daraz_item_id?: string;
    match_reason?: string;
    match_score?: number;
  }>;
};
