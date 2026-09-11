import { ComingSoonPage } from "@/components/ui/Primitives";

export function OrdersPage() {
  return (
    <ComingSoonPage
      title="Orders"
      description="Unified cross-store order management will live here in a later phase. Use Shipping for ready-to-ship labels today."
    />
  );
}

export function ProductsPage() {
  return (
    <ComingSoonPage
      title="Products"
      description="Product Hub and listing management are planned for a later phase."
    />
  );
}

export function InventoryPage() {
  return (
    <ComingSoonPage
      title="Inventory"
      description="Stock and bulk quantity updates will be added after product APIs are verified."
    />
  );
}

export function FinancePage() {
  return (
    <ComingSoonPage
      title="Finance"
      description="Transactions and payouts require Daraz finance API verification first."
    />
  );
}

export function AnalyticsPage() {
  return (
    <ComingSoonPage
      title="Analytics"
      description="Store comparison and reporting will be built on cached operational data later."
    />
  );
}

export function ConnectionsPage() {
  return (
    <ComingSoonPage
      title="Connections"
      description="Vendor-to-vendor product sharing and permissions will arrive with the Product Copy phase."
    />
  );
}
