"""Patch PostgresTenancyRepo.upsert_daraz_product for detail_* columns."""
from pathlib import Path

path = Path("src/db/repo.py")
text = path.read_text(encoding="utf-8")

start = text.find("    def upsert_daraz_product(self, payload: dict[str, Any]) -> dict[str, Any]:\n        from src.db.connection import connect")
# Prefer the Postgres class occurrence (second overall after Memory)
first = text.find("    def upsert_daraz_product(self, payload: dict[str, Any]) -> dict[str, Any]:")
second = text.find(
    "    def upsert_daraz_product(self, payload: dict[str, Any]) -> dict[str, Any]:",
    first + 1,
)
if second < 0:
    raise SystemExit("postgres upsert not found")
end = text.find("\n    _VARIANT_UPSERT_SQL = ", second)
if end < 0:
    raise SystemExit("end marker not found")

new = '''    def upsert_daraz_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        from src.db.connection import connect

        detail_complete = bool(payload.get("detail_complete", False))
        catalog_seen = payload.get("catalog_seen_at")
        detail_synced = payload.get("detail_synced_at")
        with connect() as conn:
            row = conn.execute(
                f"""
                INSERT INTO daraz_products (
                    workspace_id, store_id, daraz_item_id, title, title_en,
                    primary_category_id, primary_category_name, brand, description,
                    description_en, short_description, short_description_en,
                    package_content, status_raw, product_url, attributes_json,
                    variation_json, images_json, market_images_json, video_ref,
                    raw_json, catalog_seen_at, detail_synced_at, detail_complete,
                    synced_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s::jsonb,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s,
                    %s::jsonb, COALESCE(%s::timestamptz, NOW()), %s::timestamptz, %s,
                    NOW()
                )
                ON CONFLICT (store_id, daraz_item_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    title_en = EXCLUDED.title_en,
                    primary_category_id = EXCLUDED.primary_category_id,
                    primary_category_name = EXCLUDED.primary_category_name,
                    brand = EXCLUDED.brand,
                    description = EXCLUDED.description,
                    description_en = EXCLUDED.description_en,
                    short_description = EXCLUDED.short_description,
                    short_description_en = EXCLUDED.short_description_en,
                    package_content = EXCLUDED.package_content,
                    status_raw = EXCLUDED.status_raw,
                    product_url = EXCLUDED.product_url,
                    attributes_json = EXCLUDED.attributes_json,
                    variation_json = EXCLUDED.variation_json,
                    images_json = EXCLUDED.images_json,
                    market_images_json = EXCLUDED.market_images_json,
                    video_ref = EXCLUDED.video_ref,
                    raw_json = EXCLUDED.raw_json,
                    catalog_seen_at = COALESCE(EXCLUDED.catalog_seen_at, daraz_products.catalog_seen_at),
                    detail_synced_at = CASE
                        WHEN EXCLUDED.detail_complete THEN EXCLUDED.detail_synced_at
                        ELSE daraz_products.detail_synced_at
                    END,
                    detail_complete = CASE
                        WHEN EXCLUDED.detail_complete THEN TRUE
                        ELSE daraz_products.detail_complete
                    END,
                    synced_at = NOW(),
                    updated_at = NOW()
                RETURNING {self._PRODUCT_SELECT}
                """,
                (
                    payload["workspace_id"],
                    payload["store_id"],
                    str(payload["daraz_item_id"]),
                    payload.get("title"),
                    payload.get("title_en"),
                    _int_or_none(payload.get("primary_category_id")),
                    payload.get("primary_category_name"),
                    payload.get("brand"),
                    payload.get("description"),
                    payload.get("description_en"),
                    payload.get("short_description"),
                    payload.get("short_description_en"),
                    payload.get("package_content"),
                    payload.get("status_raw"),
                    payload.get("product_url"),
                    json.dumps(_json_or(payload.get("attributes_json"), {})),
                    json.dumps(_json_or(payload.get("variation_json"), {})),
                    json.dumps(_json_or(payload.get("images_json"), [])),
                    json.dumps(_json_or(payload.get("market_images_json"), [])),
                    payload.get("video_ref"),
                    json.dumps(payload["raw_json"])
                    if payload.get("raw_json") is not None
                    else None,
                    catalog_seen,
                    detail_synced,
                    detail_complete,
                ),
            ).fetchone()
            conn.commit()
        return self._product_row(row)

    def get_daraz_product_by_item_id(
        self, workspace_id: str, store_uuid: str, daraz_item_id: str
    ) -> dict[str, Any] | None:
        from src.db.connection import connect

        with connect() as conn:
            row = conn.execute(
                f"""
                SELECT {self._PRODUCT_SELECT}
                FROM daraz_products
                WHERE workspace_id = %s AND store_id = %s AND daraz_item_id = %s
                LIMIT 1
                """,
                (workspace_id, store_uuid, str(daraz_item_id)),
            ).fetchone()
        return self._product_row(row) if row else None
'''

path.write_text(text[:second] + new + text[end:], encoding="utf-8")
print("patched", second, end)
