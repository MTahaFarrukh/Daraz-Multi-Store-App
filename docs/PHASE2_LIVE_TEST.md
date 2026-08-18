# Phase 2 Live Daraz Pakistan Test

## Environment

* API base: `https://api.daraz.pk/rest`
* Account: `not connected`
* Seller ID: `n/a`
* Test date: 2026-08-18 19:14 UTC

Do NOT put credentials/tokens in this file.

## OAuth

* Authorization successful: NO
* Token exchange successful: NO
* Token metadata retrieved: NO

## Orders

* GetOrders successful: NO
* Ready-to-ship orders found: 0
* GetOrderItems successful: NO

### Order preview (no customer PII)

_No orders returned in preview._

* Tested order_id: `n/a`
* Tested order_item_ids: `n/a`

## Shipping Label

* GetDocument successful: NO
* HTTP status: `n/a`
* Daraz code: `n/a`
* Daraz message: `n/a`
* request_id: `n/a`
* MIME type: `n/a`
* File successfully decoded: NO
* Output file: `n/a`


## Label Verification

No label document was retrieved.

## Critical next action

Complete OAuth at `/oauth/login` then re-run `/test/live`.

Do not automatically declare the product fully validated.

## Final verdict

OAuth: FAIL
Order retrieval: FAIL
Order-item retrieval: FAIL
Shipping-label retrieval: FAIL
Label document decoding: FAIL
Seller Center parity: NOT YET TESTED


### Notes

- OAuth not completed — visit /oauth/login first.
