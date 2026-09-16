import { useEffect, useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import {
  useProductDefaults,
  useSaveProductDefaults,
} from "@/hooks/queries/useProducts";
import {
  ErrorBanner,
  PageHeader,
  SuccessBanner,
} from "@/components/ui/Primitives";

export function SettingsPage() {
  const { me } = useAuth();
  const workspaceId = me?.workspace?.id;
  const workspaceName =
    me?.memberships?.find((m) => m.workspace_id === me.workspace.id)?.workspace_name ||
    "Workspace";

  const defaultsQuery = useProductDefaults(workspaceId);
  const saveMutation = useSaveProductDefaults(workspaceId);
  const [weight, setWeight] = useState("");
  const [length, setLength] = useState("");
  const [width, setWidth] = useState("");
  const [height, setHeight] = useState("");
  const [qty, setQty] = useState("1");
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");

  useEffect(() => {
    const d = defaultsQuery.data?.defaults;
    if (!d) return;
    setWeight(d.default_package_weight != null ? String(d.default_package_weight) : "");
    setLength(d.default_package_length != null ? String(d.default_package_length) : "");
    setWidth(d.default_package_width != null ? String(d.default_package_width) : "");
    setHeight(d.default_package_height != null ? String(d.default_package_height) : "");
    setQty(
      d.default_initial_quantity != null ? String(d.default_initial_quantity) : "1"
    );
  }, [defaultsQuery.data]);

  async function saveDefaults() {
    setError("");
    setOk("");
    try {
      await saveMutation.mutateAsync({
        default_package_weight: weight === "" ? null : Number(weight),
        default_package_length: length === "" ? null : Number(length),
        default_package_width: width === "" ? null : Number(width),
        default_package_height: height === "" ? null : Number(height),
        default_initial_quantity: qty === "" ? 1 : Number(qty),
      });
      setOk("Product defaults saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  return (
    <div className="stack">
      <PageHeader
        title="Settings"
        description="Account and workspace details available from the authenticated session."
      />
      <ErrorBanner message={error} />
      <SuccessBanner message={ok} />
      <section className="card">
        <h3 className="section-title">Account</h3>
        <p style={{ margin: "0 0 0.35rem" }}>
          <strong>Email:</strong> {me?.user.email || "—"}
        </p>
        <p style={{ margin: 0, color: "var(--muted)", fontSize: "0.85rem" }}>
          User id is kept for support diagnostics and is not a secret token.
        </p>
        <p style={{ margin: "0.35rem 0 0", fontFamily: "monospace", fontSize: "0.78rem" }}>
          {me?.user.id}
        </p>
      </section>
      <section className="card">
        <h3 className="section-title">Workspace</h3>
        <p style={{ margin: "0 0 0.35rem" }}>
          <strong>Name:</strong> {workspaceName}
        </p>
        <p style={{ margin: "0 0 0.35rem" }}>
          <strong>Role:</strong> {me?.workspace.role || "—"}
        </p>
        <p style={{ margin: 0, fontFamily: "monospace", fontSize: "0.78rem" }}>
          {me?.workspace.id}
        </p>
      </section>

      <section className="card">
        <h3 className="section-title">Product Defaults</h3>
        <p className="muted-line" style={{ marginTop: 0 }}>
          Used as fallbacks for clone drafts when source package values are missing
          (and for future public/community sources). Units: kg and cm.
        </p>
        <div className="row" style={{ flexWrap: "wrap", gap: "0.75rem" }}>
          <label>
            Default Package Weight (kg)
            <input value={weight} onChange={(e) => setWeight(e.target.value)} />
          </label>
          <label>
            Length (cm)
            <input value={length} onChange={(e) => setLength(e.target.value)} />
          </label>
          <label>
            Width (cm)
            <input value={width} onChange={(e) => setWidth(e.target.value)} />
          </label>
          <label>
            Height (cm)
            <input value={height} onChange={(e) => setHeight(e.target.value)} />
          </label>
          <label>
            Initial clone quantity
            <input value={qty} onChange={(e) => setQty(e.target.value)} />
          </label>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          style={{ marginTop: "0.75rem" }}
          disabled={saveMutation.isPending}
          onClick={saveDefaults}
        >
          Save Defaults
        </button>
      </section>

      <section className="card">
        <h3 className="section-title">Security</h3>
        <p style={{ margin: 0, color: "var(--muted)" }}>
          Daraz access tokens, refresh tokens, Supabase secret keys, and database credentials are
          never shown in the browser.
        </p>
      </section>
    </div>
  );
}
