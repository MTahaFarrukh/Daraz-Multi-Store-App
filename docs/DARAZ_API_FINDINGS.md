# Daraz Multi-Store API Investigation

**Target:** Daraz Pakistan (`https://api.daraz.pk/rest`)  
**Portal:** [https://open.daraz.com/](https://open.daraz.com/)  
**Date:** 2026-08-18  
**Phase:** Investigation / proof-of-concept only (no production app built)

---

## Executive Summary

**PARTIAL — the official Daraz Open Platform API appears sufficient for a multi-store order dashboard and programmatic shipping-label retrieval, but label printing must be validated live on Pakistan production data before full development.**

| Goal | Assessment |
|------|------------|
| Multi-store order retrieval | **YES** (one app, many seller OAuth tokens) |
| Shipping-label retrieval via official API | **PARTIAL** (documented `GetDocument`; PK live parity with Seller Center not yet proven in this POC) |
| Combined cross-store printing | **PARTIAL** (technically feasible if labels decode to PDF/HTML; client-side merge/print) |
| Official API sufficient for product | **PARTIAL** (pending live PK verification + app approval) |

Daraz Open Platform is Lazada-derived. Pakistan uses country-specific endpoints documented in official Daraz docs. Order and document APIs are mapped in the official migration guide (`treeId=754`). Newer Lazada-only fulfillment endpoints (`/order/fulfill/pack`, PrintAWB at `/order/package/document/get`) are **not** listed in Daraz’s official API name mapping and are treated as **unverified for Daraz PK** in this investigation.

---

## Authentication

### How sellers authorize an application

1. Developer registers at [open.daraz.com](https://open.daraz.com/) and creates an app (ERP type recommended).
2. App Console provides **App Key** (`client_id`) and **App Secret**.
3. Seller opens OAuth URL in a browser, signs into Seller Center, and approves scopes.
4. Platform redirects to your **callback URL** with `?code=...`.
5. App exchanges code via **`/auth/token/create`** (no access token required for this call).
6. Store returned **`access_token`** and **`refresh_token`**.

**Pakistan authorize URL (official):**

```
https://api.daraz.pk/oauth/authorize?response_type=code&force_auth=true&redirect_uri={CALLBACK}&client_id={APP_KEY}
```

**Manual step required in POC:** Browser OAuth cannot be fully automated without a registered callback server. The POC builds the URL; a human completes login/authorize; the app captures `code` and exchanges it for tokens.

### App Key / App Secret

| Item | Role |
|------|------|
| **App Key** | Public application identifier (`app_key` query param, OAuth `client_id`) |
| **App Secret** | Private signing key for HMAC-SHA256; never sent in requests |

### Access token

| Item | Detail |
|------|--------|
| Parameter | `access_token` on every seller-data API call |
| Obtained via | `/auth/token/create` with OAuth `code` |
| Scopes | Implicit in seller authorization at login |

### Refresh token

| Item | Detail |
|------|--------|
| Endpoint | `/auth/token/refresh` |
| When | Before access token expiry; only if `refresh_expires_in > 0` |
| Important | Each refresh returns a **new** `refresh_token` — persist the latest |

### Token expiration (official)

| App status | Access token (`expires_in`) | Refresh token (`refresh_expires_in`) |
|------------|----------------------------|--------------------------------------|
| **Test** | 7 days | 30 days |
| **Online** | 30 days | 180 days |

Authorization `code` expires in **30 minutes**.

Official guidance: refresh ~30 minutes before access token expiry.

### Multi-seller / multi-store authorization

| Question | Answer |
|----------|--------|
| Can one application be authorized by many sellers? | **Yes** — same `app_key`, different OAuth flows |
| Does each store get its own token? | **Yes** — one `access_token` pair per authorized seller account |
| Can one token access multiple unrelated stores? | **No** — token is scoped to the authorizing seller |

Cross-border (`country=cb`) is a Lazada pattern; Daraz PK local sellers authorize via `api.daraz.pk`.

### Request signing (exact requirements)

Every HTTP call includes:

| Parameter | Required | Notes |
|-----------|----------|-------|
| `app_key` | Yes | |
| `timestamp` | Yes | Milliseconds; within ~7200s of UTC |
| `sign_method` | Yes | `sha256` (HMAC-SHA256) |
| `sign` | Yes | Uppercase hex |
| `access_token` | Yes* | *Except `/auth/token/create` and `/auth/token/refresh` |

**Algorithm:** sort params by name → concatenate `key+value` → prefix with API path → HMAC-SHA256 with App Secret → uppercase hex. POST requests append JSON body before hashing.

**Example (sanitized GetOrder):**

```
GET https://api.daraz.pk/rest/order/get
  ?app_key=123456
  &access_token=SANITIZED
  &timestamp=1517820392000
  &sign_method=sha256
  &order_id=1234
  &sign=4190D32361CFB9581350222F345CB77F3B19F0E31D162316848A2C1FFD5FAB4A
```

---

## Store Authorization

### How the API identifies the store

The **`access_token`** identifies which seller store data is returned. There is no separate `store_id` parameter on order APIs.

Token response includes (official sample):

```json
{
  "access_token": "SANITIZED",
  "refresh_token": "SANITIZED",
  "expires_in": 259200,
  "refresh_expires_in": 259200,
  "account_platform": "seller_center",
  "account": "seller@example.com",
  "country_user_info": [
    { "country": "pk", "seller_id": "1001", "user_id": 10101 }
  ]
}
```

Use `country_user_info[].seller_id` and `account` to label connected stores in your app. Error messages reference a **GetSeller** API for verifying token/store alignment, but the official Daraz migration mapping lists **`/seller/update`** (SellerUpdate), not a documented `/seller/get` path — treat seller profile retrieval as **limited to token metadata** unless confirmed in App Console API Explorer.

### Multiple stores on one application

**Supported architecture:**

```
One Daraz App (app_key + app_secret)
  ├── Store A → OAuth → access_token_A + refresh_token_A
  ├── Store B → OAuth → access_token_B + refresh_token_B
  └── Store C → OAuth → access_token_C + refresh_token_C
```

Each store’s orders/labels are fetched with that store’s token against the same `https://api.daraz.pk/rest` base URL.

---

## Orders API

### GetOrders — list orders

| | |
|--|--|
| **Method** | GET |
| **Path** | `/orders/get` |
| **Full URL** | `https://api.daraz.pk/rest/orders/get` |

**Required / common parameters:**

| Parameter | Required | Example | Description |
|-----------|----------|---------|-------------|
| `created_after` | Yes* | `2026-01-01T00:00:00+05:00` | ISO 8601 lower bound (*or `update_after`) |
| `status` | Optional | `ready_to_ship` | Filter; use `all` for every status |
| `limit` | Optional | `100` | Max **100** per request |
| `offset` | Optional | `0` | Pagination; max offset **5000** |
| `sort_by` | Optional | `created_at` | `created_at` or `updated_at` |
| `sort_direction` | Optional | `ASC` | `ASC` or `DESC` |

**Important response fields:**

| Field | Description |
|-------|-------------|
| `data.countTotal` | Total matching orders for filter |
| `data.count` | Count in current page |
| `data.orders[]` | Order list |
| `data.orders[].order_id` | Store-scoped order ID |
| `data.orders[].order_number` | Display order number |
| `data.orders[].statuses[]` | Item-level statuses (deduplicated set) |
| `data.orders[].created_at` | Order creation time |
| `data.orders[].address_shipping` | Shipping address object |

**Sanitized example response fragment:**

```json
{
  "code": "0",
  "request_id": "abc123",
  "data": {
    "countTotal": 42,
    "count": 2,
    "orders": [
      {
        "order_id": "123456789",
        "order_number": "123456789",
        "statuses": ["ready_to_ship"],
        "created_at": "2026-08-01T10:00:00+05:00",
        "items_count": 2,
        "price": "2500.00"
      }
    ]
  }
}
```

### GetOrder — single order

| | |
|--|--|
| **Method** | GET |
| **Path** | `/order/get` |
| **Parameter** | `order_id` |

### GetOrderItems — line items (critical for labels)

| | |
|--|--|
| **Method** | GET |
| **Path** | `/order/items/get` |
| **Parameter** | `order_id` |

**Important response fields per item:**

| Field | Use |
|-------|-----|
| `order_item_id` | **Required for GetDocument** |
| `order_id` | Parent order |
| `status` | e.g. `pending`, `packed`, `ready_to_ship` |
| `package_id` | Package identifier (newer flows) |
| `tracking_code` | AWB / tracking after pack |
| `shipment_provider` | Carrier name |

### GetMultipleOrderItems — batch (≤50 orders)

| | |
|--|--|
| **Method** | GET |
| **Path** | `/orders/items/get` |
| **Parameter** | `order_ids=[id1,id2]` |

---

## Ready-to-Ship Orders

### Filtering ready orders

Use **GetOrders** with `status=ready_to_ship`.

Official supported status values include:  
`unpaid`, `pending`, `packed`, `canceled`, `ready_to_ship`, `delivered`, `returned`, `shipped`, `failed`, `topack`, `toship`, `shipping`, `lost`, and others (see GetOrders error documentation).

Daraz has **no single order-level status** — each line item has its own status; `statuses[]` on the order is a set of item statuses.

### Fulfillment state machine (official)

From Order Status Flow documentation:

| Transition | Allowed item statuses |
|------------|----------------------|
| **`/order/pack`** (SetStatusToPackedByMarketplace) | `pending`, `repacked` only |
| **`/order/rts`** (SetStatusToReadyToShip) | `pending`, `repacked`, `packed` |

**Typical Seller Center / API path:**

```
pending → pack (/order/pack) → packed → RTS (/order/rts) → ready_to_ship → print label
```

Sandbox Seller Center test flow (official): **To Pack → print only → packed → Ready To Ship button → RTS**.

Identifiers involved:

| ID | When available |
|----|----------------|
| `order_id` | From order creation |
| `order_item_id` | From GetOrderItems |
| `package_id` | After successful pack (also on order items) |
| `tracking_number` | After pack with Daraz logistics |

---

## Shipping Labels

### Official API: GetDocument (verified for Daraz Open Platform)

| | |
|--|--|
| **Method** | GET |
| **Path** | `/order/document/get` |
| **Maps from** | Legacy Seller Center `GetDocument` |
| **Purpose** | Retrieve order documents: **invoice**, **shippingLabel**, **carrierManifest** |

**This is the primary officially mapped shipping-label API for Daraz Open Platform.**

| Question | Answer |
|----------|--------|
| Does it return a shipping label? | **Yes** — when `doc_type=shippingLabel` and preconditions met |
| PDF, image, or URL? | **Base64-encoded file** in response; `mime_type` indicates format (commonly `text/html` or `application/pdf`) — **not a direct URL** |
| Requires order/package ID? | Requires **`order_item_ids`** array |
| Multiple orders per call? | Multiple **order items** in one request (`order_item_ids=[id1,id2]`) |
| Before shipment? | **No** — items must be **packed or RTS**; error **E034** if not |
| Same as Seller Center label? | **Intended yes** (platform-generated AWB); **PK live parity not verified in this POC** |

**Required parameters:**

| Parameter | Value |
|-----------|-------|
| `doc_type` | `shippingLabel` |
| `order_item_ids` | `[279709,279710]` (string array format) |

**Response structure:**

```json
{
  "code": "0",
  "data": {
    "document": {
      "document_type": "shippingLabel",
      "mime_type": "text/html",
      "file": "BASE64_ENCODED_CONTENT..."
    }
  }
}
```

Decode `file` from base64; interpret per `mime_type`.

**Official precondition (FAQ):** Call **SetStatusToReadyToShip** (`/order/rts`) before GetDocument, otherwise **E034**. Error **30012** states status must be `"packed"` or `"ready to ship"`. Error **700040** if order is unpaid/pending/canceled or SOF/DBS. Error **50008** for SOF orders — Daraz does not provide platform shipping labels.

**Sanitized request:**

```
GET https://api.daraz.pk/rest/order/document/get
  ?app_key=...
  &access_token=...
  &timestamp=...
  &sign_method=sha256
  &doc_type=shippingLabel
  &order_item_ids=[102612420,102612419]
  &sign=...
```

### Newer Lazada PrintAWB (NOT verified for Daraz PK)

Lazada’s newer fulfillment docs describe **PrintAWB** at **`/order/package/document/get`** (POST, `package_id`, `doc_type=PDF|HTML`, returns `file` + optional `pdf_url`). This path is **not** in the official Daraz migration API name table (`treeId=754`). Do **not** assume it works on `api.daraz.pk` until verified in App Console API Explorer or live testing.

---

## Documents

| doc_type | Purpose |
|----------|---------|
| `shippingLabel` | AWB / shipping label |
| `invoice` | Invoice document |
| `carrierManifest` | Carrier manifest |

All use the same **`/order/document/get`** endpoint with different `doc_type`.

---

## Multi-Store Support

**YES** — architecturally straightforward:

1. Single registered app at open.daraz.com.
2. Per store: run OAuth → store `{store_label, seller_id, access_token, refresh_token, token_expiry}` locally (encrypted file/DB in future phase — not built now).
3. Poll or webhook (optional) per store using that store’s token.
4. Aggregate orders in application memory / future DB.

**Caveat:** Each store owner must authorize the app (or you authorize each of your own stores separately). Daraz does not provide a single token for unrelated seller accounts.

---

## Printing Feasibility

| Category | Assessment |
|----------|------------|
| **A. API directly provides printable labels** | **PARTIAL** — GetDocument returns base64 HTML/PDF content, not a print-ready URL in the legacy API |
| **B. API provides documents we convert/process** | **YES** — decode base64 → save `.html` or `.pdf` → browser/OS print |
| **C. API does NOT provide labels** | **No** for standard Daraz-logistics orders; **yes** for SOF/DBS seller-own-fleet edge cases |
| **D. Only Seller Center / private APIs** | **Not required** for standard case if GetDocument works on PK live data |

**Proposed combined workflow (future phase):**

```
For each connected store token:
  GetOrders(status=ready_to_ship)
  → GetOrderItems(order_id)
  → GetDocument(doc_type=shippingLabel, order_item_ids=[...])
  → decode & collect PDF/HTML files
→ merge PDFs client-side (or print queue per file)
→ user clicks Print All
```

Batch limits: paginate orders at 100; batch order items prudently per GetDocument call.

---

## Rate Limits

Official Daraz documentation does **not** publish a fixed global limit (e.g. “500/min”) in `treeId=754` docs. Instead:

| Source | Limit |
|--------|-------|
| Platform Rules | Limits depend on **application category** template |
| Developer Agreement | Daraz may limit API calls at sole discretion |
| Penalty tier | Restricted apps: **1,000 calls/day** |
| Hibernation | **1 call/day** if app inactive 90 days |
| GetOrders | **100** orders max per request; offset max **5000** |
| GetMultipleOrderItems | **50** orders max per request |
| Throttling | Documented warning: excessive GetOrders polling may be throttled |

Monitor success/failure rates in App Console dashboard.

---

## Restrictions

| Topic | Detail |
|-------|--------|
| App approval | Developer account + app review; **Apply Online** for production |
| Sandbox testing | Loan test accounts; sandbox “Get Token”; **1000+ calls/day at ≥95% success for 2 weeks** before online approval |
| ERP app type | Official manual recommends ERP for broad API permissions |
| Data storage | Developer Agreement + Data Protection Policy apply; store tokens securely |
| Commercial / multi-store | Allowed as ISV/ERP integrator model; no doc prohibiting multi-seller apps |
| SOF/DBS orders | No platform shipping label via GetDocument |
| Technical support | **support-api@daraz.pk** (official FAQ) |

---

## Official API Endpoints

| Capability | Official Endpoint | Available? | Notes |
|------------|-------------------|------------|-------|
| OAuth authorize (PK) | `https://api.daraz.pk/oauth/authorize` | YES | Browser step |
| Create access token | `/auth/token/create` | YES | System API, no access_token |
| Refresh token | `/auth/token/refresh` | YES | System API |
| List orders | `/orders/get` | YES | limit max 100 |
| Single order | `/order/get` | YES | |
| Order line items | `/order/items/get` | YES | Returns order_item_id |
| Batch line items | `/orders/items/get` | YES | Max 50 order IDs |
| Shipping label (legacy) | `/order/document/get` | YES* | *doc_type=shippingLabel; packed/RTS required |
| Invoice | `/order/document/get` | YES* | doc_type=invoice |
| Carrier manifest | `/order/document/get` | YES* | doc_type=carrierManifest |
| Mark packed | `/order/pack` | YES | POST; pending/repacked items |
| Ready to ship | `/order/rts` | YES | POST; pending/repacked/packed |
| Shipment providers | `/shipment/providers/get` | YES | Before pack |
| Cancel order | `/order/cancel` | YES | |
| Seller update | `/seller/update` | YES | Not a read profile API |
| Pack (new Lazada flow) | `/order/fulfill/pack` | UNVERIFIED (PK) | In Lazada docs only |
| PrintAWB (new) | `/order/package/document/get` | UNVERIFIED (PK) | PDF/HTML + pdf_url in Lazada docs |
| RTS (new package flow) | `/order/package/rts` | UNVERIFIED (PK) | Lazada fulfillment docs |

---

## Evidence

| Conclusion | Official source |
|------------|-----------------|
| PK API base URL | [Daraz migration — Request URL `https://api.daraz.{country}/rest`](https://open.alitrip.com/docs/doc.htm?articleId=120243&docType=1&treeId=754) |
| API path mapping (GetDocument, GetOrders, pack, rts) | [Daraz migration — API name mapping](https://open.alitrip.com/docs/doc.htm?articleId=120243&docType=1&treeId=754) — also [Lazada mirror docId=108139](https://open.lazada.com/apps/doc/doc?docId=108139&nodeId=10397) |
| OAuth PK authorize URL | [Seller authorization introduction](https://open.alitrip.com/docs/doc.htm?articleId=120222&docType=1&treeId=754) |
| Token lifetimes | [Seller authorization introduction — expires_in table](https://open.alitrip.com/docs/doc.htm?articleId=120222&docType=1&treeId=754) |
| HMAC-SHA256 signing | [Signature algorithm](https://open.alitrip.com/docs/doc.htm?articleId=120322&docType=1&treeId=754) |
| GetDocument requires RTS/packed | [API Related Questions — GetDocument FAQ](https://developer.alibaba.com/docs/doc.htm?treeId=754&articleId=120225&docType=1) |
| GetDocument purpose (labels/invoices) | [Order API overview (Lazada/Daraz family)](https://open.alitrip.com/docs/doc.htm?articleId=108147&docType=1&treeId=499) |
| GetOrders pagination & status filter | [Get order list tutorial](https://open.alitrip.com/docs/doc.htm?articleId=121327&docType=1&treeId=499) |
| pack/rts status prerequisites | [Order Status Flow](https://doc.alidayu.com/docs/doc.htm?articleId=120167&docType=1&treeId=499) |
| Fulfillment tutorial (pack → PrintAWB → RTS) | [Fulfillment orders — Lazada](https://open.alitrip.com/docs/doc.htm?articleId=121328&docType=1&treeId=499) — **Lazada; verify separately on Daraz PK** |
| Platform rules / rate penalties | [Platform Rules and Penalty Measures](https://open.alitrip.com/docs/doc.htm?articleId=120231&docType=1&treeId=754) |
| Sandbox / go-live requirements | [Test your application](https://open.alitrip.com/docs/doc.htm?articleId=120236&docType=1&treeId=754) |
| App registration steps | [How to access Daraz Open API](https://open.alitrip.com/docs/doc.htm?articleId=121263&docType=1&treeId=754) |
| API support contact | [API Related Questions — support-api@daraz.pk](https://developer.alibaba.com/docs/doc.htm?treeId=754&articleId=120225&docType=1) |

---

## Final Architecture Recommendation

**Do not build the full dashboard yet.** Next steps based only on verified findings:

### Phase 2 — Live verification (required)

1. Register ERP app at [open.daraz.com](https://open.daraz.com/) with callback URL.
2. Authorize **two real PK seller stores** (your Store A / Store B) via OAuth.
3. Run `src/daraz_api.py` against **production** (not sandbox) with real ready-to-ship orders:
   - Confirm `GetOrders` + `GetOrderItems` return expected data.
   - Confirm `GetDocument(shippingLabel)` returns decodable HTML/PDF.
   - **Visually compare** one API label with Seller Center print for the same `order_item_id`.
4. If legacy `/order/pack` + `/order/rts` fail but Seller Center works, test whether newer `/order/fulfill/pack` exists on PK (App Console API Explorer).

### Phase 3 — Minimal integration prototype (only after Phase 2 passes)

- Local encrypted token store (one row per store).
- Token refresh job.
- CLI: `list-stores`, `fetch-orders`, `fetch-labels`, `print-all`.
- PDF merge utility for combined printing.

### Do not pursue (yet)

- Full web UI, deployment, or database-heavy architecture until label parity is confirmed on PK production.

---

## VERDICT:

* **Multi-store order retrieval:** YES  
* **Shipping-label retrieval:** PARTIAL  
* **Combined printing:** PARTIAL  
* **Official API sufficient for our product:** PARTIAL  

---

*Investigation performed without real credentials or modifications to any Daraz seller account.*
