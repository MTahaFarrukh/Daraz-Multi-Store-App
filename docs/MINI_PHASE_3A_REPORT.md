# Mini Phase 3A Report — Label Processing Engine

**Date:** 2026-08-19  
**Scope:** Local document processing only (no Daraz API, no dashboard)

---

## Implementation

Built a small shipping-label document processing engine that:

- Defines a typed `LabelDocument` model with store and order metadata
- Decodes base64 PDF/HTML content (Daraz `GetDocument`-compatible shape)
- Merges multiple PDF labels into one combined PDF using `pypdf`
- Sorts labels deterministically (store name ? order ID ? order item ID)
- Provides a CLI to generate synthetic test labels, list them, and merge them
- Includes pytest coverage for decoding, merging, ordering, and error handling
- Exposes a pluggable `html_to_pdf()` abstraction with clear Windows limitations

No Daraz credentials, OAuth changes, or production API calls were made.

---

## Files Added

| File | Purpose |
|------|---------|
| `src/label_processor.py` | Core decode, sort, merge engine |
| `src/label_cli.py` | CLI: generate-test-labels, list-labels, merge-labels |
| `tests/test_label_processor.py` | Automated pytest suite (9 tests) |
| `docs/LABEL_PROCESSING.md` | Processor documentation |
| `docs/MINI_PHASE_3A_REPORT.md` | This report |
| `data/test_labels/store_a/.gitkeep` | Directory placeholder |
| `data/test_labels/store_b/.gitkeep` | Directory placeholder |
| `data/test_labels/store_c/.gitkeep` | Directory placeholder |
| `data/output/.gitkeep` | Output directory placeholder |

**Generated at runtime (gitignored):**

- `data/test_labels/store_*/ORDER-*__ITEM-*.pdf` (5 synthetic labels)
- `data/output/combined-test-labels.pdf`

---

## Dependencies Added

Added to `requirements.txt`:

| Package | Purpose |
|---------|---------|
| `pypdf>=5.0.0` | PDF read/merge |
| `reportlab>=4.0.0` | Synthetic test PDF generation only |
| `pytest>=8.0.0` | Automated tests |

**Not added:** `weasyprint` (optional; HTML?PDF requires extra system deps on Windows)

---

## Tests

| Metric | Result |
|--------|--------|
| Tests run | 9 |
| Passed | 9 |
| Failed | 0 |

Coverage includes:

1. Base64 PDF decoding
2. Invalid base64 error
3. PDF merge (2 labels)
4. Five labels ? five pages
5. Output readable by pypdf / page dimensions preserved
6. Deterministic ordering
7. Metadata association
8. Unsupported MIME type error
9. HTML conversion unavailable error

Command:

```powershell
pytest -v
```

---

## PDF Merge Result

| Metric | Value |
|--------|-------|
| Input labels | 5 |
| Output pages | 5 |
| Output file | `data/output/combined-test-labels.pdf` |

Merge order verified:

```text
Store A: ORDER-001 / ITEM-001
Store A: ORDER-002 / ITEM-002
Store B: ORDER-003 / ITEM-003
Store B: ORDER-004 / ITEM-004
Store C: ORDER-005 / ITEM-005
```

Page count verified programmatically with `pypdf`.

---

## HTML Support

| Capability | Status |
|------------|--------|
| Accept `text/html` mime type in model | YES |
| Decode base64 HTML content | YES |
| `html_to_pdf()` interface | YES |
| Default HTML?PDF on Windows | **NOT AVAILABLE** without WeasyPrint + system libraries |
| PDF-only merge workflow | **FULLY FUNCTIONAL** |

HTML labels can be pre-converted to PDF before merge, or WeasyPrint can be installed later. See `docs/LABEL_PROCESSING.md`.

---

## Daraz Integration Readiness

When Daraz developer registration is approved and Phase 2 live testing succeeds:

```text
GET /order/document/get (doc_type=shippingLabel)
        ?
response.data.document.mime_type
response.data.document.file (base64)
        ?
decode_label_document(mime_type, base64, LabelMetadata(...))
        ?
LabelDocument (per store, per order item)
        ?
sort_labels() across stores
        ?
merge_labels() ? combined PDF
```

Existing `DarazClient.get_shipping_label()` and `save_document()` remain unchanged. A future thin adapter will map API responses into `LabelMetadata` + `decode_label_document()` without modifying merge logic.

---

## Known Limitations

- HTML?PDF not enabled by default on Windows (WeasyPrint dependency chain)
- No user-defined print ordering UI (only programmatic custom sort key hook)
- No automatic OS printing (by design)
- Test labels are synthetic — not Daraz-format AWB layouts
- Multi-page per-order labels merge all pages but were not tested with real Daraz documents
- `reportlab` used only for test generation, not production label rendering

---

## FINAL STATUS

Label decoding: PASS  
PDF merging: PASS  
Metadata handling: PASS  
Test label generation: PASS  
Automated tests: PASS  
Combined PDF generation: PASS  
Ready for Daraz API integration: YES

---

*Phase complete. Do not proceed to dashboard or multi-store application until Daraz API live verification (Phase 2) is done.*
