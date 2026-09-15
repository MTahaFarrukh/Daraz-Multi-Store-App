import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "@/hooks/useAuth";
import { ProtectedRoute } from "@/components/auth/ProtectedRoute";
import { AppLayout } from "@/components/layout/AppLayout";
import { LoginPage, SignupPage } from "@/pages/AuthPages";
import { OverviewPage } from "@/pages/OverviewPage";
import { PerformancePage } from "@/pages/PerformancePage";
import { ShippingPage } from "@/pages/ShippingPage";
import { StoresPage } from "@/pages/StoresPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { OrdersPage } from "@/pages/OrdersPage";
import {
  AnalyticsPage,
  ConnectionsPage,
  FinancePage,
  InventoryPage,
  ProductsPage,
} from "@/pages/PlaceholderPages";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />

        <Route element={<ProtectedRoute />}>
          <Route path="/app" element={<AppLayout />}>
            <Route index element={<OverviewPage />} />
            <Route path="performance" element={<PerformancePage />} />
            <Route path="orders" element={<OrdersPage />} />
            <Route path="products" element={<ProductsPage />} />
            <Route path="inventory" element={<InventoryPage />} />
            <Route path="shipping" element={<ShippingPage />} />
            <Route path="finance" element={<FinancePage />} />
            <Route path="analytics" element={<AnalyticsPage />} />
            <Route path="stores" element={<StoresPage />} />
            <Route path="connections" element={<ConnectionsPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
        </Route>

        <Route path="/" element={<Navigate to="/app" replace />} />
        <Route path="*" element={<Navigate to="/app" replace />} />
      </Routes>
    </AuthProvider>
  );
}
