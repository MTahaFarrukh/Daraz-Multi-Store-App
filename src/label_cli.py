"""
CLI for label processing — local test workflow only (no Daraz API).

Commands:
  generate-test-labels
  list-labels
  merge-labels
"""

from __future__ import annotations

import argparse
import sys
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from src.label_processor import (
    OUTPUT_DIR,
    TEST_LABELS_DIR,
    discover_test_labels,
    merge_labels,
    pdf_page_count,
    sort_labels,
)

# Synthetic test data — no real customer information.
TEST_STORES: dict[str, str] = {
    "store_a": "Store A",
    "store_b": "Store B",
    "store_c": "Store C",
}

TEST_LABEL_SPECS: list[dict] = [
    {
        "store_id": "store_a",
        "order_id": "ORDER-001",
        "order_item_id": "ITEM-001",
        "tracking": "TRK-TEST-001",
        "pagesize": A4,
    },
    {
        "store_id": "store_a",
        "order_id": "ORDER-002",
        "order_item_id": "ITEM-002",
        "tracking": "TRK-TEST-002",
        "pagesize": letter,
    },
    {
        "store_id": "store_b",
        "order_id": "ORDER-003",
        "order_item_id": "ITEM-003",
        "tracking": "TRK-TEST-003",
        "pagesize": A4,
    },
    {
        "store_id": "store_b",
        "order_id": "ORDER-004",
        "order_item_id": "ITEM-004",
        "tracking": "TRK-TEST-004",
        "pagesize": (400, 600),
    },
    {
        "store_id": "store_c",
        "order_id": "ORDER-005",
        "order_item_id": "ITEM-005",
        "tracking": "TRK-TEST-005",
        "pagesize": letter,
    },
]

COMBINED_OUTPUT = OUTPUT_DIR / "combined-test-labels.pdf"


def _draw_test_label(
    pagesize: tuple[float, float],
    store_name: str,
    order_id: str,
    order_item_id: str,
    tracking: str,
) -> bytes:
    """Create a single-page synthetic test PDF label."""
    buffer = BytesIO()
    width, height = pagesize
    pdf = canvas.Canvas(buffer, pagesize=pagesize)

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(20 * mm, height - 25 * mm, "TEST LABEL — NOT FOR SHIPPING")

    pdf.setFont("Helvetica", 12)
    y = height - 40 * mm
    lines = [
        f"Store: {store_name}",
        f"Test Order ID: {order_id}",
        f"Test Order Item ID: {order_item_id}",
        f"Test Tracking: {tracking}",
        f"Page size: {int(width)} x {int(height)} pt",
    ]
    for line in lines:
        pdf.drawString(20 * mm, y, line)
        y -= 8 * mm

    pdf.setFont("Helvetica-Oblique", 10)
    pdf.drawString(20 * mm, 15 * mm, "Synthetic test document — not a real shipment label")

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def cmd_generate_test_labels(_: argparse.Namespace) -> int:
    """Generate synthetic PDF test labels under data/test_labels/."""
    created = 0
    for spec in TEST_LABEL_SPECS:
        store_id = spec["store_id"]
        store_name = TEST_STORES[store_id]
        filename = f"{spec['order_id']}__{spec['order_item_id']}.pdf"
        target_dir = TEST_LABELS_DIR / store_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / filename

        pdf_bytes = _draw_test_label(
            pagesize=spec["pagesize"],
            store_name=store_name,
            order_id=spec["order_id"],
            order_item_id=spec["order_item_id"],
            tracking=spec["tracking"],
        )
        target_path.write_bytes(pdf_bytes)
        created += 1
        print(f"  created {target_path.relative_to(TEST_LABELS_DIR.parent.parent)}")

    print(f"\nGenerated {created} test label(s) in {TEST_LABELS_DIR}")
    return 0


def cmd_list_labels(_: argparse.Namespace) -> int:
    """List discovered test labels grouped by store."""
    labels = discover_test_labels(store_names=TEST_STORES)
    if not labels:
        print("No test labels found. Run: python -m src.label_cli generate-test-labels")
        return 1

    current_store: str | None = None
    for label in labels:
        if label.store_name != current_store:
            current_store = label.store_name
            print(f"\n{current_store} ({label.store_id})")
        print(
            f"  {label.order_id} / {label.order_item_id}  "
            f"[{label.source_filename}, {label.mime_type}, {label.page_count()} page(s)]"
        )

    print(f"\nTotal: {len(labels)} label(s)")
    return 0


def cmd_merge_labels(args: argparse.Namespace) -> int:
    """Merge all test labels into data/output/combined-test-labels.pdf."""
    labels = discover_test_labels(store_names=TEST_STORES)
    if not labels:
        print("No test labels found. Run: python -m src.label_cli generate-test-labels")
        return 1

    output_path = Path(args.output) if args.output else COMBINED_OUTPUT
    result = merge_labels(labels, output_path)
    pages = pdf_page_count(result)

    print(f"Merged {len(labels)} label(s) -> {result}")
    print(f"Output pages: {pages}")
    print("\nMerge order:")
    for label in sort_labels(labels):
        print(f"  {label.store_name}: {label.order_id} / {label.order_item_id}")

    if pages != len(labels):
        print(
            f"\nWarning: expected {len(labels)} pages (one per label), got {pages}",
            file=sys.stderr,
        )
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Daraz Multi-Store — label processing CLI (local test files only)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-test-labels", help="Create synthetic test PDF labels")
    gen.set_defaults(func=cmd_generate_test_labels)

    lst = sub.add_parser("list-labels", help="List discovered test labels by store")
    lst.set_defaults(func=cmd_list_labels)

    merge = sub.add_parser("merge-labels", help="Merge test labels into one PDF")
    merge.add_argument(
        "--output",
        help=f"Output PDF path (default: {COMBINED_OUTPUT})",
    )
    merge.set_defaults(func=cmd_merge_labels)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
