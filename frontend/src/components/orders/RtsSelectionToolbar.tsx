import { countVisiblePrintState } from "@/lib/printLabelStatus";
import type { PrintableOrderLike } from "@/lib/printLabelStatus";

type Props<T extends PrintableOrderLike> = {
  orders: T[];
  selectedCount: number;
  lastBatchLabel?: string | null;
  onSelectAll: () => void;
  onSelectUnprinted: () => void;
  onClear: () => void;
  disabled?: boolean;
};

export function RtsSelectionToolbar<T extends PrintableOrderLike>({
  orders,
  selectedCount,
  lastBatchLabel,
  onSelectAll,
  onSelectUnprinted,
  onClear,
  disabled = false,
}: Props<T>) {
  const { unprinted, printed } = countVisiblePrintState(orders);
  if (!orders.length) return null;

  return (
    <div className="rts-selection-toolbar" role="region" aria-label="RTS selection">
      <div className="row rts-selection-actions">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={disabled || !orders.length}
          onClick={onSelectAll}
        >
          Select All
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={disabled || unprinted === 0}
          onClick={onSelectUnprinted}
        >
          Select Unprinted
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={disabled || selectedCount === 0}
          onClick={onClear}
        >
          Clear Selection
        </button>
      </div>
      <p className="muted-line rts-selection-stats">
        <strong>{unprinted}</strong> Unprinted · <strong>{printed}</strong> Printed ·{" "}
        <strong>{selectedCount}</strong> Selected
        {lastBatchLabel ? (
          <>
            {" "}
            · Last successful label batch: <strong>{lastBatchLabel}</strong>
          </>
        ) : null}
      </p>
      <p className="muted-line" style={{ margin: 0 }}>
        Recommended: Select Unprinted → Print Labels. Select All never silently reprints.
      </p>
    </div>
  );
}
