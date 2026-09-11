import { Menu, LogOut } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { useNavigate } from "react-router-dom";

export function AppHeader({
  title,
  subtitle,
  onMenu,
}: {
  title: string;
  subtitle?: string;
  onMenu: () => void;
}) {
  const { me, signOut } = useAuth();
  const navigate = useNavigate();

  return (
    <header className="app-header">
      <div className="row">
        <button type="button" className="menu-btn btn-ghost" onClick={onMenu} aria-label="Open menu">
          <Menu size={18} />
        </button>
        <div>
          <h1>{title}</h1>
          {subtitle ? <p className="subtitle">{subtitle}</p> : null}
        </div>
      </div>
      <div className="header-actions">
        <span className="subtitle" style={{ display: "none" }} />
        <span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>
          {me?.user.email || "Account"}
        </span>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={async () => {
            await signOut();
            navigate("/login", { replace: true });
          }}
        >
          <LogOut size={14} />
          Log out
        </button>
      </div>
    </header>
  );
}
