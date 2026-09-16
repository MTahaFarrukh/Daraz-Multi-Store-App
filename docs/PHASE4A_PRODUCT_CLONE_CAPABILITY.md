# Phase 4A — Product Clone Capability Spike

**Date:** 2026-09-16  
**Marketplace:** Daraz Pakistan (`https://api.daraz.pk/rest`)  
**Live store probed:** `mtfdigitalemporiumofficial_gmail_com` (legacy token vault)  
**Catalog size seen:** `total_products = 408`  
**Constraint honored:** No intentional CreateProduct of a real listing. Empty-payload validation probe only.  
**Evidence artifacts (sanitized):** `data/phase4a_spike_results.json`, `data/phase4a_spike_followup.json`, `data/phase4a_spike_brands_migrate.json`  
**Spike scripts:** `scripts/phase4a_product_spike.py`, `scripts/phase4a_product_spike_followup.py`, `scripts/phase4a_brands_migrate.py`

---

## Overall feasibility

| Path | Verdict | Confidence |
|------|---------|------------|
| **Connected Store → Destination Store** | **SUPPORTED** (with draft + validation + image migrate) | **High** (read path live-proven; create path API-reachable, payload unproven end-to-end) |
| **Public Daraz URL → Destination Store** | **PARTIAL** | **Medium-Low** for “exact copy”; seller APIs cannot read foreign items |
| **Community Snapshot → Destination Store** | **Architecture feasible** | Design-only this phase; no Communities infra yet |

### One-line summary

Connected-store cloning is the only path that can approach “as close as Daraz allows.” Public URL import cannot use seller GetProductItem for other sellers’ items and must not be sold as exact clone without a separate approved extraction strategy. Community sources should share **snapshots**, never tokens.

---

## Codebase audit (before any schema/UI)

### Existing (reuse)

| Area | Location | Note |
|------|----------|------|
| Signing / REST client | `src/daraz_api.py` | HMAC-SHA256 `_request`; extend here — do not fork |
| Store client factory | `src/ops.py` `client_for_store` | Same as orders |
| Token refresh | `src/token_refresh.py` | Refresh-before-call pattern from `order_sync` |
| Workspace authz | `src/auth` + `get_workspace_context` | Mandatory on all product APIs |
| Order sync template | `src/order_sync.py` | Pagination / per-store partial failure |
| DB tenancy pattern | `src/db/schema.sql`, `repo.py` | `(workspace_id, store_id, daraz_*)` uniqueness |
| Products route shell | `/app/products` → `PlaceholderPages.ProductsPage` | Coming Soon only |
| TanStack keys | `frontend/src/lib/queryKeys.ts` | Commented `products` key ready |
| Settings shell | `SettingsPage.tsx` | Read-only today; host package defaults later |
| Binary URL download | `DarazClient.download_binary_url` | Labels today; may help pull image bytes |

### Missing (greenfield in 4B+)

- Any product/category/image/brand client methods
- `daraz_products` / variants / media tables
- Product Hub UI, clone draft APIs, SKU generator service
- Workspace package-default persistence
- Community product contracts

### Current app permissions (live)

Proven reachable with this app’s seller token:

- `GET /products/get` ✅  
- `GET /product/item/get` ✅ (own `item_id`)  
- `GET /category/tree/get` ✅  
- `GET /category/attributes/get` ✅  
- `GET /category/brands/query` ✅ (`startRow` + `pageSize`)  
- `POST /images/migrate` and `POST /image/migrate` ✅ (accepted; returns `batch_id`)  
- `POST /product/create` ✅ **API exists** (empty XML → `E001: Parameter PrimaryCategory is mandatory` — no product created)

Not found / invalid path on PK for this app:

- `/brands/get`, `/product/brands/get` → `InvalidApiPath`
- `/category/suggestion/get`, `/product/category/suggestion/get` → `InvalidApiPath`

Partial / unclear:

- `GET /image/response/get` → requires `batch_id` but returned `E005: Invalid Request Format` for the migrate `batch_id` we tried (poll contract **unproven**)

---

## GetProducts findings

**Endpoint:** `GET /products/get`  
**Live call:** `filter=all`, `limit=2` / `50`, `offset=0`, `options=1` → `code=0`

### Proven response fields

| Field | Present live? | Notes |
|-------|---------------|-------|
| `item_id` | ✅ | e.g. `1974026524` |
| `primary_category` | ✅ | e.g. `10002730` |
| `status` | ✅ | e.g. `Active` |
| `images[]` | ✅ | Daraz CDN URLs (`static-01.daraz.pk`) |
| `attributes.name` / `name_en` | ✅ | Bilingual titles |
| `attributes.description` / `description_en` | ✅ | Rich HTML (`article.lzd-article`) |
| `attributes.short_description_en` | ✅ | Rich HTML bullets |
| `attributes.brand` | ✅ | String e.g. `No Brand` |
| `attributes.warranty_type` | ✅ | |
| `attributes.Hazmat` | ✅ | |
| `attributes.video` | ⚠️ | **Not always** on list payload; seen on GetProductItem |
| `attributes.package_content` | ❌ in first 50 scan | Attribute schema exists; 0/50 products had it filled |
| `created_time` / `updated_time` | ✅ | Epoch-ms strings |
| `total_products` | ✅ | Pagination support |
| SKU: `SellerSku`, `ShopSku`, `SkuId` | ✅ | |
| SKU: `price`, `special_price`, dates | ✅ | |
| SKU: `quantity` / `Available` / warehouse stocks | ✅ | |
| SKU: `package_weight/length/width/height` | ✅ | Strings; weight looks like **kg**, dims **cm** |
| SKU: `saleProp` | ✅ | e.g. `{ "color_family": "Pink" }` |
| SKU: `Images` | ✅ key | Often `[]` when gallery is product-level |
| SKU: `Url` | ✅ | Public PDP URL |
| QC status | ❌ | Not seen in these payloads |

### List vs detail

List (`/products/get`) is already rich enough for hub sync. Detail (`/product/item/get`) adds `variation`, `marketImages`, and sometimes `attributes.video`.

---

## GetProductItem findings

**Endpoint:** `GET /product/item/get`

| Probe | Result |
|-------|--------|
| Own `item_id=1974026524` | ✅ Full product |
| Own `seller_sku=MTF-POPSICLE-Squishy` **without** item_id | ❌ `4191` — **ItemId is required** |
| Foreign `item_id=1000123456` | ❌ `207 E207: SKU not exist` |
| Multi-variant own item (`1962226787`, 5 SKUs) | ✅ `variation.Variation1` + per-SKU `saleProp` |

### Extra vs list

- `data.variation` — sale attribute definition (`name`, `label`, `hasImage`, `options[]`)
- `data.marketImages`
- `attributes.video` — platform video id string (e.g. `"7100016814542"`), **not** a playable URL
- Description HTML can include `<img>` (`desc_has_img: true` on sampled item)

### Ownership conclusion

Seller product read APIs are **own-catalog only**. They cannot power public URL import of third-party listings.

---

## CreateProduct findings

| Claim | Status |
|-------|--------|
| API path exists on PK | **Proven** (`POST /product/create`) |
| App can invoke it | **Proven** (got business validation, not InvalidApiPath / auth deny) |
| Successful create of a listing | **Unproven** (intentionally not done) |
| Payload format | **Strongly indicated:** Lazada-style XML in `payload` query/business param (JSON body → `IncompleteSignature`) |
| Required `PrimaryCategory` | **Proven** (empty product → `E001: Parameter PrimaryCategory is mandatory`) |

### Category-mandatory fields (live leaf `10002730`)

From `GET /category/attributes/get` — **13 mandatory**:

**Normal:** `name`, `name_en`, `short_description`, `short_description_en`, `description_en`, `warranty_type`, `brand`  

**SKU:** `SellerSku`, `price`, `package_weight`, `package_length`, `package_width`, `package_height`

**Optional but relevant:** `description` (richText), `video` (text), `package_content` (sku text), `quantity` (**not mandatory** — `is_mandatory=0`)

### QC behavior

Unproven live. Expect Seller Center QC after create (standard Lazada/Daraz). Draft UI must surface “submitted → pending QC” as a fidelity warning until proven.

---

## Category / attribute requirements

| API | Live |
|-----|------|
| `/category/tree/get` | ✅ Tree with `category_id`, `name`, `leaf`, `children`, `var` |
| `/category/attributes/get?primary_category_id=` | ✅ `is_mandatory`, `input_type`, `attribute_type` (`normal`/`sku`), `is_sale_prop`, options |
| `/category/brands/query` | ✅ Requires `startRow` + `pageSize`; returns `module[].brand_id/name` |
| Category suggestion APIs | ❌ InvalidApiPath on tested paths |

### Pre-CreateProduct validation flow (recommended)

```
source primary_category
  → confirm destination category (same id or mapped)
  → GET /category/attributes/get
  → map source attributes + saleProp / variation
  → resolve brand via /category/brands/query (or allow "No Brand" if accepted)
  → list missing mandatory + unsupported
  → block Create until draft.valid
```

Do **not** submit invalid payloads blindly.

---

## Images

| Capability | Verdict |
|------------|---------|
| Read product images | ✅ CDN URLs on product + sometimes SKU |
| CreateProduct accepts raw external URLs | **Unproven** — do not assume |
| `/images/migrate` + `/image/migrate` | ✅ Accepted with XML `payload` containing `<Image><Url>…</Url></Image>` |
| Migrate response | Returns top-level `batch_id` / `code=0` |
| `/image/response/get` poll | **Unproven** (`E005` with that batch_id) |
| Upload binary `/image/upload` | Not probed this spike |

### Media pipeline (4B design)

```
source URLs → dedupe/validate → migrate to Daraz → wait/resolve hosted URLs → attach to draft → CreateProduct
```

Until `image/response/get` is proven, Phase 4B must include a dedicated migrate-poll spike before enabling Create.

---

## Description

- Live descriptions are **rich HTML** (`richText` attribute type).
- Structure uses `article.lzd-article` / inline styles / `<span>` / lists.
- Bilingual: local `description` + mandatory `description_en` (for this category).
- Safe approach: sanitize to an allowlist (`p`, `ul`, `ol`, `li`, `br`, `strong`, `em`, `img[src]`) — never scripts/iframes.

**HTML acceptance of arbitrary markup:** Partially proven by reading existing listings; write-path acceptance still unproven until a controlled create.

---

## Description images

- Some live descriptions already contain `<img>`.
- User requirement (append product images into description when missing) is **design-feasible** after migrate produces Daraz-hosted URLs.
- Rules for 4B: prefer main + secondary gallery; skip duplicates / tiny assets; do not dump all SKU images blindly.

---

## Video

| Question | Live finding |
|----------|--------------|
| Returned by GetProducts? | Rarely (1/50 in scan had attr; detail more reliable) |
| Returned by GetProductItem? | ✅ as `attributes.video` = **opaque platform id** |
| Playable URL? | ❌ not in seller payload |
| CreateProduct accepts video? | Attribute exists (`input_type=text`, optional) — **write unproven** |
| Public page exposes usable video? | Unreliable in spike (no solid video id extraction) |
| Auto-clone video? | **Not supported** until migrate/upload path for video is proven |

**Draft warning copy:** “Video detected but automatic video cloning is not supported.”  
Do not block clone solely for missing video.

---

## Variants

- Proven: multi-SKU items with `saleProp.color_family` and `variation.Variation1`.
- Product-level `images` + optional SKU `Images`.
- Prices/stock/dimensions are **per SKU**.
- Clone draft must regenerate **SellerSku per variant**; preserve sale props / variation options.

---

## Seller SKU restrictions

| Question | Finding |
|----------|---------|
| Is `MTF-…` valid? | **YES — proven.** Live catalog already uses `MTF-POPSICLE-Squishy`, `MTF-BUTTER-SQUISHY`, etc. |
| Spaces allowed? | **YES — proven** (`TumblerStoppers-Pack of 2 Flower`, len 32) |
| Max length | Not hard-failed at 32; official max still **docs-unconfirmed** — design generator with conservative limit (e.g. 50) and collision suffixes |
| Must regenerate on clone? | **Yes** (product rule) — never copy source SellerSku into destination |

Centralize in one backend helper, default prefix `MTF-`, future workspace override.

---

## Weight & dimensions

| Field | Mandatory (cat 10002730) | Live units (inferred) |
|-------|--------------------------|------------------------|
| `package_weight` | ✅ | kg (`0.08`, `0.09`) |
| `package_length/width/height` | ✅ | cm (`10.00`, `5.00`, `4.00`) |

### Policy (confirmed by this spike)

| Source | Rule |
|--------|------|
| Connected store | Copy source SKU dimensions when valid |
| Public URL | **Do not invent** — public page did **not** expose package weight/dims in HTML probe → workspace defaults |
| Community | Workspace defaults unless future verified snapshot permits otherwise |
| Missing after fallback | **Block CreateProduct** |

---

## Public URL investigation

### 1) Item ID from URL

**Yes.** Patterns work:

- `…-i{itemId}.html`
- `…/products/i{itemId}.html`
- Optional `-s{skuId}` segment

Live: `https://www.daraz.pk/products/i1974026524.html` → `1974026524`.

### 2) Official seller API for arbitrary PDP?

**No.** Own-token `GetProductItem` on foreign id → `E207`.

### 3) Public page data (owned PDP fetch, 200, ~247KB)

Present:

- JSON-LD `Product` (name, category path, brand, images, text description, sku/mpn)
- Embedded signals: `skuInfos`, `app.run`, `moduleData`, `pdpTrackingData`
- Many CDN image URLs

Absent / unreliable:

- `package_weight` / dimensions / package_content
- Reliable video asset URL
- Full attribute schema / mandatory category attribute ids
- Stable structured API equivalent to GetProductItem

### 4) Anti-bot / SSR

- Simple browser UA fetch returned 200 for a real PDP.
- Earlier probe flagged `blocked_hint` inconsistently; treat scraping as **fragile**.
- SSRF risk if user URLs are fetched server-side — must allowlist `*.daraz.pk` / known Daraz hosts only.

### 5) Policy

Importing third-party listing content may conflict with Daraz terms / IP. Spike does **not** authorize scraping implementation.

### Verdict

**PARTIAL / not exact-copy ready.**  
Do **not** implement brittle scraping in 4B without explicit approval. Safest near-term: URL → extract item id → **only proceed if that item belongs to a connected store** (deep-link into connected-copy flow). True third-party URL import = separate decision.

---

## Duplicate detection inputs

Available after destination sync:

| Signal | Usefulness |
|--------|------------|
| Regenerated SellerSku | Weak alone (always new) |
| Source `item_id` stored on draft metadata | Strong for re-copy detection |
| Title + `primary_category` similarity | Soft warning |
| Image fingerprint / first image URL | Soft |
| GTIN/barcode | Not seen in live payloads sampled |

**v1 recommendation:** before create, warn if destination has same normalized title+category or same `source_item_id` previously cloned; never silent create.

---

## Price / initial quantity recommendation

| Field | Recommendation |
|-------|----------------|
| Price / special_price | Copy into draft from source; user can edit before create |
| Quantity | **Do not auto-mirror live sellable stock** |
| Safe v1 | Workspace **initial quantity default** (e.g. `1` or `5`), editable on draft; `quantity` is **not mandatory** on probed category but should still be set intentionally |
| Stock ops | Separate Inventory phase |

---

## Security / SSRF

| Rule | Requirement |
|------|-------------|
| Connected products | Workspace-private; never cross-workspace read |
| Public URL fetch | Allowlist Daraz hosts only; block localhost/private IPs; no open redirects off-allowlist |
| Community | Snapshot references only; never source store tokens |
| Tokens | Never to browser (`sanitize_store_view`) |

---

## Proposed ProductCloneDraft

Normalized shape (conceptual — finalize after 4B migrate-poll proof):

```ts
type ProductCloneDraft = {
  source_type: "connected_store" | "daraz_url" | "community";
  source: {
    workspace_id?: string;      // connected only
    store_id?: string;          // connected only
    daraz_item_id?: string;
    public_url?: string;
    community_product_id?: string;
  };
  destination_store_id: string; // slug or uuid within workspace

  product: {
    title: string;
    title_en?: string;
    primary_category: number;
    brand: string;
    brand_id?: number;
    attributes: Record<string, unknown>;
    short_description_html?: string;
    description_html?: string;
    package_content?: string;
    warranty_type?: string;
  };

  media: {
    product_images: string[];          // after migrate: Daraz-hosted
    variant_images: Record<string, string[]>;
    description_images: string[];
    video?: { source_id?: string; status: "unsupported" | "pending" | "ready" };
  };

  variants: Array<{
    sale_props: Record<string, string>;
    price: number;
    special_price?: number | null;
    quantity: number;                  // from defaults, not blind copy
    seller_sku: string;                // MTF- generated
    package_weight: number;
    package_length: number;
    package_width: number;
    package_height: number;
    source_seller_sku?: string;        // audit only
  }>;

  package_resolution: {
    source: "connected_source" | "workspace_default" | "missing";
    warnings: string[];
  };

  validation: {
    missing_mandatory: string[];
    unsupported: string[];
    warnings: string[];
    fidelity: {
      copied: string[];
      changed_by_multistore: string[];
      not_available: string[];
    };
    can_create: boolean;
  };
};
```

---

## Proposed DB schema (draft — do not finalize until 4B)

```text
daraz_products
  id UUID PK
  workspace_id
  store_id          -- FK daraz_stores.id
  daraz_item_id TEXT
  title, title_en
  primary_category
  brand
  status_raw
  attributes_json JSONB
  description_html, short_description_html
  package_content
  images_json JSONB
  video_ref TEXT NULL
  raw_json JSONB NULL          -- optional debug, redact later
  synced_at
  UNIQUE (store_id, daraz_item_id)

daraz_product_variants
  id UUID PK
  product_id FK
  workspace_id, store_id
  daraz_sku_id
  shop_sku
  seller_sku
  sale_props_json JSONB
  price, special_price
  quantity
  package_length/width/height/weight
  images_json JSONB
  synced_at
  UNIQUE (store_id, daraz_sku_id)

workspace_product_defaults
  workspace_id PK
  default_package_weight
  default_package_length
  default_package_width
  default_package_height
  default_initial_quantity
  sku_prefix TEXT DEFAULT 'MTF-'
  updated_at
```

Media/attributes may stay JSONB until migrate/create shapes stabilize.

---

## Proposed API endpoints (4B+, not implemented)

```
GET  /api/products
GET  /api/products/{id}
POST /api/products/sync
GET  /api/product-defaults
PUT  /api/product-defaults

POST /api/products/clone/draft          # connected | url | community
POST /api/products/clone/validate
POST /api/products/clone/create         # gated; only after migrate+validate proven
```

All workspace-scoped. Empty store selection ≠ all stores (same as Orders).

---

## Proposed UI flow

1. **Product Hub** (`/app/products`): Sync Products + table (image, title, store, variants, price, stock, status, last sync) + View / Copy (copy enabled only for connected sources).
2. **Add from Daraz Link**: paste URL → if item owned by a connected store, open connected draft; if foreign, show **capability warning** and stop (until approved extraction).
3. **Copy**: destination store picker → draft preview with fidelity checklist → Copy & Edit / Create Copy (create gated).
4. **Settings → Product Defaults**: package weight/dims + initial qty + future SKU prefix.

---

## Risks / blockers

1. **CreateProduct end-to-end unproven** (XML schema, QC, limits).  
2. **Image migrate → usable URL poll unproven** (`/image/response/get`).  
3. **Public URL exact clone unsupported** via official seller APIs.  
4. **Video not clonable** with current evidence.  
5. **Brand resolution** needs careful `/category/brands/query` usage (`name` filter behavior looked weak in one probe).  
6. **Category-specific attributes** differ; one category’s mandatory set ≠ all.  
7. **Only one local vault store** used for live spike; destination-store create needs a second connected store for real clone E2E.  
8. **Terms/IP** for third-party URL import.

---

## Recommendation for Phase 4B

**Proceed with a narrow 4B — Connected Store Product Hub + Clone Draft (no blind Create, no Communities, no scraping).**

### 4B in scope

1. Daraz client methods: `get_products`, `get_product_item`, `get_category_tree`, `get_category_attributes`, `query_brands`, `migrate_images`.  
2. Local `daraz_products` / variants sync for connected stores.  
3. Product Hub read UI + Sync.  
4. `ProductCloneDraft` builder for connected → connected.  
5. Central `MTF-` SKU generator + package dimension resolver + workspace defaults in Settings.  
6. Description sanitize + optional description-image append **in draft only**.  
7. Duplicate soft-check.  
8. Fidelity / warning UI.  
9. **Hard gate:** CreateProduct button behind feature flag until (a) migrate-poll returns hosted URLs and (b) one supervised create succeeds on a designated test store.

### 4B out of scope

- Public URL scraping / third-party exact import  
- Communities  
- Video cloning  
- Inventory mirroring  

### Explicit stops (per brief)

- Public Daraz URL → exact/reliable extraction: **not ready** — report only; do not hack scraping into “done.”  
- Video: detect + warn only.

---

## Proven vs unproven cheat sheet

| Topic | Proven live | Unproven |
|-------|-------------|----------|
| GetProducts field inventory | ✅ | QC fields |
| GetProductItem own catalog | ✅ | seller_sku-only lookup |
| Foreign product via seller API | ❌ impossible | — |
| Category tree/attributes | ✅ | suggestion APIs |
| Brands query | ✅ | perfect name search |
| Image migrate accept | ✅ | response poll → final URL |
| CreateProduct API access | ✅ validation error | successful create / QC |
| MTF- SellerSku | ✅ | official max length doc |
| Package dims on connected SKUs | ✅ | units officially documented |
| Public PDP JSON-LD subset | ✅ | package dims, stable full JSON parse, anti-bot longevity |
| Video id on own product | ✅ | recreating video on create |

---

**STOP. Phase 4A complete. No Phase 4B implementation in this deliverable.**
