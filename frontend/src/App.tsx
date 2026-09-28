import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "@/hooks/useAuth";
import { ProtectedRoute } from "@/components/auth/ProtectedRoute";
import { AppLayout } from "@/components/layout/AppLayout";
import { AppErrorBoundary } from "@/components/ErrorBoundary";
import { LoadingState } from "@/components/ui/Primitives";
import { LoginPage, SignupPage } from "@/pages/AuthPages";

const OverviewPage = lazy(() =>
  import("@/pages/OverviewPage").then((m) => ({ default: m.OverviewPage }))
);
const PerformancePage = lazy(() =>
  import("@/pages/PerformancePage").then((m) => ({ default: m.PerformancePage }))
);
const ShippingPage = lazy(() =>
  import("@/pages/ShippingPage").then((m) => ({ default: m.ShippingPage }))
);
const StoresPage = lazy(() =>
  import("@/pages/StoresPage").then((m) => ({ default: m.StoresPage }))
);
const SettingsPage = lazy(() =>
  import("@/pages/SettingsPage").then((m) => ({ default: m.SettingsPage }))
);
const OrdersPage = lazy(() =>
  import("@/pages/OrdersPage").then((m) => ({ default: m.OrdersPage }))
);
const ProductsPage = lazy(() =>
  import("@/pages/ProductsPage").then((m) => ({ default: m.ProductsPage }))
);
const ConnectionsPage = lazy(() =>
  import("@/pages/ConnectionsPage").then((m) => ({ default: m.ConnectionsPage }))
);
const InventoryPage = lazy(() =>
  import("@/pages/InventoryPage").then((m) => ({ default: m.InventoryPage }))
);
const AnalyticsPage = lazy(() =>
  import("@/pages/AnalyticsPage").then((m) => ({ default: m.AnalyticsPage }))
);
const FinancePage = lazy(() =>
  import("@/pages/FinancePage").then((m) => ({ default: m.FinancePage }))
);

function LazyPage({ children }: { children: ReactNode }) {
  return <Suspense fallback={<LoadingState label="Loading page…" />}>{children}</Suspense>;
}

export default function App() {
  return (
    <AppErrorBoundary>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<SignupPage />} />

          <Route element={<ProtectedRoute />}>
            <Route path="/app" element={<AppLayout />}>
              <Route
                index
                element={
                  <LazyPage>
                    <OverviewPage />
                  </LazyPage>
                }
              />
              <Route
                path="performance"
                element={
                  <LazyPage>
                    <PerformancePage />
                  </LazyPage>
                }
              />
              <Route
                path="orders"
                element={
                  <LazyPage>
                    <OrdersPage />
                  </LazyPage>
                }
              />
              <Route
                path="products"
                element={
                  <LazyPage>
                    <ProductsPage />
                  </LazyPage>
                }
              />
              <Route
                path="inventory"
                element={
                  <LazyPage>
                    <InventoryPage />
                  </LazyPage>
                }
              />
              <Route
                path="shipping"
                element={
                  <LazyPage>
                    <ShippingPage />
                  </LazyPage>
                }
              />
              <Route
                path="finance"
                element={
                  <LazyPage>
                    <FinancePage />
                  </LazyPage>
                }
              />
              <Route
                path="analytics"
                element={
                  <LazyPage>
                    <AnalyticsPage />
                  </LazyPage>
                }
              />
              <Route
                path="stores"
                element={
                  <LazyPage>
                    <StoresPage />
                  </LazyPage>
                }
              />
              <Route
                path="connections"
                element={
                  <LazyPage>
                    <ConnectionsPage />
                  </LazyPage>
                }
              />
              <Route
                path="settings"
                element={
                  <LazyPage>
                    <SettingsPage />
                  </LazyPage>
                }
              />
            </Route>
          </Route>

          <Route path="/" element={<Navigate to="/app" replace />} />
          <Route path="*" element={<Navigate to="/app" replace />} />
        </Routes>
      </AuthProvider>
    </AppErrorBoundary>
  );
}
