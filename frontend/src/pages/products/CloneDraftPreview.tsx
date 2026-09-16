import type { ProductCloneDraftResponse } from "@/types/api";

export function CloneDraftPreview({
  result,
  variants,
}: {
  result: ProductCloneDraftResponse;
  variants: Array<Record<string, any>>;
}) {
  const fidelity = result.fidelity || {};
  const draft = result.draft as Record<string, any>;
  const media = draft.media || {};
  const enhancement = media.description_enhancement || {};
  return (
    <div className="stack" style={{ marginTop: "0.75rem" }}>
      <h4 style={{ margin: 0 }}>Clone Ready</h4>
      <p className="muted-line" style={{ margin: 0 }}>
        {variants.length} variant(s) · {(media.product_images || []).length} image(s) · Creation
        remains gated
      </p>

      <div>
        <strong>WILL COPY</strong>
        <ul>
          {(fidelity.copied || []).map((x) => (
            <li key={x}>✓ {x}</li>
          ))}
        </ul>
        <strong>WILL CHANGE</strong>
        <ul>
          {(fidelity.changed_by_multistore || []).map((x) => (
            <li key={x}>• {x}</li>
          ))}
        </ul>
        <strong>NOT AUTOMATICALLY COPIED</strong>
        <ul>
          {(fidelity.not_available || []).map((x) => (
            <li key={x}>• {x}</li>
          ))}
        </ul>
      </div>

      <p style={{ margin: 0 }}>
        Description enhancement:{" "}
        {enhancement.had_existing_images
          ? "✓ Existing description images preserved"
          : enhancement.enhancement === "appended"
            ? "✓ Product images will be included in description"
            : enhancement.message || "—"}
      </p>

      {(result.warnings || []).length ? (
        <div className="banner banner-info">
          {(result.warnings || []).map((w) => (
            <div key={w}>⚠ {w}</div>
          ))}
        </div>
      ) : null}
      {(result.errors || []).length ? (
        <div className="banner banner-error">
          {(result.errors || []).map((w) => (
            <div key={w}>✕ {w}</div>
          ))}
        </div>
      ) : null}
      {(result.possible_duplicates || []).length ? (
        <div className="banner banner-info">
          Possible duplicate(s) on destination:
          <ul>
            {result.possible_duplicates.map((d) => (
              <li key={d.id}>
                {d.title} ({d.match_reason})
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <h4 style={{ margin: "0.35rem 0 0" }}>Generated Seller SKUs</h4>
      <ul>
        {variants.map((v) => (
          <li key={v.seller_sku}>
            <code>{v.seller_sku}</code> · PKR {v.price} · qty {v.quantity} · pkg{" "}
            {v.package_weight}/{v.package_length}×{v.package_width}×{v.package_height}
          </li>
        ))}
      </ul>

      <button
        type="button"
        className="btn btn-ghost"
        disabled
        title="Gated until supervised A→B create proof"
      >
        Create Copy (gated)
      </button>
    </div>
  );
}
