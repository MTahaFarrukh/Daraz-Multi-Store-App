# Phase 4D.5.2 — Automatic Required Category Attributes

## Problem

After 4D.5.1 price resolution, CreateProduct still failed with:

- `category_missing:normal:short_description`
- `category_missing:normal:short_description_en`
- `category_missing:sku:color_family`

Root cause: validation ran against the destination category schema, but the draft never auto-filled required attributes that were already present (or safely derivable) in source extract data.

## Fix

New module `src/category_attr_resolve.py`:

1. Cache category attribute schema by `marketplace/category_id`
2. Deterministically resolve required **normal** + **SKU** attributes from ProductCloneDraft
3. Special handlers: `short_description` / `short_description_en`, `color_family` per SKU
4. Wire **before** `validate_draft_against_category` in `product_add` and `product_create`
5. Instrument `category_attributes_ms` + `attribute_resolution_ms`

## Safety

Never invent material / size / gender / warranty / model / capacity when source has no reliable value → `NEEDS_ATTENTION`.

## Pre-flight order

source extract → variant prices → duplicate check → category → brand/No Brand → **auto-resolve required attrs** → validate → CreateProduct
