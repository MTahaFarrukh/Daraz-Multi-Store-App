import { PageHeader } from "@/components/ui/Primitives";
import { StorePerformancePanel } from "@/components/performance/StorePerformancePanel";

export function PerformancePage() {
  return (
    <div>
      <PageHeader
        title="Store Performance"
        description="Monthly workspace rankings from Daraz order metrics."
      />
      <StorePerformancePanel />
    </div>
  );
}
