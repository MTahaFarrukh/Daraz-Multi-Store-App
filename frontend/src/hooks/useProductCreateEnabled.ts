import { useEffect, useState } from "react";
import { loadPublicConfig } from "@/lib/supabase";

/**
 * Non-secret create gate from /api/public-config.
 * `null` while loading; UI should not enable Create/Start until `true`.
 * Backend still enforces ALLOW_PRODUCT_CREATE independently.
 */
export function useProductCreateEnabled(): boolean | null {
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    void loadPublicConfig()
      .then((cfg) => {
        if (!cancelled) setEnabled(Boolean(cfg.product_create_enabled));
      })
      .catch(() => {
        // Fail closed for create actions; preview-only flows can still work.
        if (!cancelled) setEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return enabled;
}
