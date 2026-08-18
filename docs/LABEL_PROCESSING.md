# Label Processing Engine

Documentation for the local shipping-label document processor (Mini Phase 3A).

## Purpose

The label processor prepares shipping-label documents from multiple stores for combined printing. It:

- Decodes base64-encoded label content (as returned by Daraz `GetDocument`)
- Normalizes PDF and HTML labels into PDF bytes
- Merges multiple single- or multi-page labels into one PDF
- Preserves original page dimensions and content without resizing or rotation

This phase uses **synthetic local test files only**. No Daraz API calls are made.

## Input

Future Daraz integration will supply:

| Field | Source |
|-------|--------|
| `mime_type` | `data.document.mime_type` from `/order/document/get` |
| `base64_content` | `data.document.file` |
| Metadata | Store context + `order_id` + `order_item_id` |

Example:

```python
from src.label_processor import LabelMetadata, decode_label_document

label = decode_label_document(
    mime_type="application/pdf",
    base64_content=document["file"],
    metadata=LabelMetadata(
        store_id="store_a",
        store_name="Store A",
        order_id="123456789",
        order_item_id="987654321",
        source_filename="shippingLabel.pdf",
    ),
)
```

Supported MIME types:

- `application/pdf`
- `text/html`

## PDF processing

1. Base64 content is decoded with strict validation.
2. PDF bytes are read with `pypdf` without modification.
3. `merge_labels()` appends each page from each label in sorted order.
4. Page dimensions (`mediabox`) from source PDFs are preserved.

## HTML processing

HTML labels are supported via a pluggable converter:

```python
from src.label_processor import html_to_pdf

pdf_bytes = html_to_pdf(html_bytes)
```

**Current status on Windows:**

- HTML → PDF requires **WeasyPrint** (`pip install weasyprint`) plus system libraries (GTK/Pango/Cairo), which are often difficult on Windows.
- By default, an `UnavailableHtmlConverter` is used and raises a clear `HtmlConversionError`.
- **PDF merging is fully functional** without WeasyPrint.
- Pre-convert HTML labels to PDF before merge, or install WeasyPrint when ready.

Optional install (Linux/macOS or Windows with dependencies):

```powershell
pip install weasyprint
```

## Ordering

Default deterministic sort key:

1. `store_name`
2. `order_id`
3. `order_item_id`

```python
from src.label_processor import sort_labels, merge_labels

ordered = sort_labels(labels)          # default key
merge_labels(ordered, "combined.pdf")  # merges in that order
```

A custom sort key can be passed later for user-selected ordering without UI changes:

```python
merge_labels(labels, output_path, sort_key=my_custom_key)
```

## Test workflow

From project root:

```powershell
cd daraz-multi-store
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python -m src.label_cli generate-test-labels
python -m src.label_cli list-labels
python -m src.label_cli merge-labels
```

Expected output:

- Test labels: `data/test_labels/store_{a,b,c}/*.pdf`
- Combined PDF: `data/output/combined-test-labels.pdf` (5 pages)

Run automated tests:

```powershell
pytest
```

## Future Daraz integration

When Daraz developer access is approved:

```text
Daraz /order/document/get response
        ↓
  mime_type + base64 file + order metadata
        ↓
  decode_label_document() → LabelDocument
        ↓
  sort_labels() by store/order
        ↓
  merge_labels() → combined PDF
        ↓
  User prints combined PDF (manual step in this phase)
```

The existing `daraz_api.py` client already decodes documents via `DarazClient.save_document()`. A thin adapter will map API responses to `LabelMetadata` + `decode_label_document()` without changing the merge logic.

## File layout

```text
src/label_processor.py   — core engine
src/label_cli.py         — local test CLI
data/test_labels/        — synthetic per-store labels
data/output/             — merged PDF output
tests/test_label_processor.py
```
