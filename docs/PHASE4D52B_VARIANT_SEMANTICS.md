# Phase 4D.5.2B — Generic Variant Semantics + Always No Brand

## Variant semantics

Public extract now preserves `dimensions[]` per SKU:

- `source_property_id` / `source_property_name`
- `source_value_id` / `source_value`
- `semantic_type` (color | size | pack | capacity | model | style | other)

Mapping is semantic only:

- Color → `color_family` (when destination has it)
- Pack → `pack_size` / pack attrs (never `color_family`)
- Size → `size`
- Capacity → `capacity`

Cross-semantic writes are forbidden. Schema incompatibility yields a clear
`variant_schema_conflict:…` message instead of `category_invalid:color_family`.

## Brand policy

Destination brand is **always** the category’s valid **No Brand** entry.
Source brand is provenance only (`source_brand_ignored`). Cached by
`marketplace/category_id`. If No Brand is unavailable → `no_brand_unavailable`.
