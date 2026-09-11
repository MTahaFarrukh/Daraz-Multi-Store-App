import { useAuth } from "@/hooks/useAuth";
import { PageHeader } from "@/components/ui/Primitives";

export function SettingsPage() {
  const { me } = useAuth();
  const workspaceName =
    me?.memberships?.find((m) => m.workspace_id === me.workspace.id)?.workspace_name ||
    "Workspace";

  return (
    <div className="stack">
      <PageHeader
        title="Settings"
        description="Account and workspace details available from the authenticated session."
      />
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
        <h3 className="section-title">Security</h3>
        <p style={{ margin: 0, color: "var(--muted)" }}>
          Daraz access tokens, refresh tokens, Supabase secret keys, and database credentials are
          never shown in the browser.
        </p>
      </section>
    </div>
  );
}
