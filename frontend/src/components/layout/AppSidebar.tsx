import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  ShoppingBag,
  Package,
  Warehouse,
  Truck,
  Wallet,
  BarChart3,
  Store,
  Link2,
  Settings,
  X,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";

const primary = [
  { to: "/app", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/app/orders", label: "Orders", icon: ShoppingBag },
  { to: "/app/products", label: "Products", icon: Package },
  { to: "/app/inventory", label: "Inventory", icon: Warehouse },
  { to: "/app/shipping", label: "Shipping", icon: Truck },
  { to: "/app/finance", label: "Finance", icon: Wallet },
  { to: "/app/analytics", label: "Analytics", icon: BarChart3 },
];

const secondary = [
  { to: "/app/stores", label: "Stores", icon: Store },
  { to: "/app/connections", label: "Connections", icon: Link2 },
  { to: "/app/settings", label: "Settings", icon: Settings },
];

export function AppSidebar({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { me } = useAuth();
  const workspaceName =
    me?.memberships?.find((m) => m.workspace_id === me.workspace.id)?.workspace_name ||
    "Workspace";

  return (
    <>
      {open ? <div className="sidebar-backdrop" onClick={onClose} aria-hidden /> : null}
      <aside className={`app-sidebar${open ? " open" : ""}`}>
        <div className="brand row" style={{ justifyContent: "space-between" }}>
          <div>
            <p className="brand-name">MultiStore</p>
            <p className="brand-sub">Daraz operations</p>
          </div>
          <button type="button" className="menu-btn" onClick={onClose} aria-label="Close menu">
            <X size={18} />
          </button>
        </div>

        <nav className="nav-section" aria-label="Primary">
          <p className="nav-label">Workspace</p>
          {primary.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
              onClick={onClose}
            >
              <item.icon />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <nav className="nav-section" aria-label="Manage">
          <p className="nav-label">Manage</p>
          {secondary.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
              onClick={onClose}
            >
              <item.icon />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="workspace-chip">
            <strong>{workspaceName}</strong>
            {me?.user.email || "Signed in"} · {me?.workspace.role || "member"}
          </div>
        </div>
      </aside>
    </>
  );
}
