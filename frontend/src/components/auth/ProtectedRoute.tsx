import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import { LoadingState, ErrorBanner } from "@/components/ui/Primitives";

export function ProtectedRoute() {
  const { loading, authenticated, error } = useAuth();
  const location = useLocation();

  if (loading) return <LoadingState label="Loading workspace…" />;
  if (!authenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (error) {
    return (
      <div className="content">
        <ErrorBanner message={error} />
      </div>
    );
  }
  return <Outlet />;
}
