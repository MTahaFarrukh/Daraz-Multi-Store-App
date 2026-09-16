import { useMemo, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { AppSidebar } from "@/components/layout/AppSidebar";
import { AppHeader } from "@/components/layout/AppShell";

const titles: Record<string, { title: string; subtitle?: string }> = {
  "/app": { title: "Overview", subtitle: "Action-first workspace summary" },
  "/app/orders": { title: "Orders" },
  "/app/products": { title: "Products" },
  "/app/inventory": { title: "Inventory" },
  "/app/shipping": { title: "Shipping", subtitle: "Load RTS and print labels" },
  "/app/finance": { title: "Finance" },
  "/app/analytics": { title: "Analytics" },
  "/app/stores": { title: "Stores", subtitle: "Connected Daraz stores and groups" },
  "/app/connections": { title: "Connections" },
  "/app/settings": { title: "Settings" },
};

export function AppLayout() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const meta = useMemo(() => {
    return titles[location.pathname] || { title: "MultiStore" };
  }, [location.pathname]);

  return (
    <div className="app-shell">
      <AppSidebar open={open} onClose={() => setOpen(false)} />
      <div className="app-main">
        <AppHeader
          title={meta.title}
          subtitle={meta.subtitle}
          onMenu={() => setOpen(true)}
        />
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
