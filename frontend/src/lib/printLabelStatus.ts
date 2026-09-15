/**
 * Label print status from MultiStore print events only.
 * Never classify from order.created_at vs last batch time.
 */

export type PrintLabelTone = "ok" | "warn" | "danger" | "muted";

export type PrintLabelInfo = {
  kind: "unprinted" | "printed" | "reprinted" | "none";
  text: string;
  tone: PrintLabelTone;
  printCount: number;
  lastPrintedAt: string | null;
};

export type PrintableOrderLike = {
  id: string;
  has_print?: boolean | null;
  print_count?: number | null;
  last_printed_at?: string | null;
  status_group?: string | null;
};

export function formatPrintTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Authoritative: events only (has_print / print_count). */
export function resolvePrintLabelStatus(o: PrintableOrderLike): PrintLabelInfo {
  const count = Number(o.print_count || 0);
  const hasPrint = Boolean(o.has_print) || count > 0;
  const last = o.last_printed_at || null;
  const time = formatPrintTime(last);

  if (hasPrint && count > 1) {
    return {
      kind: "reprinted",
      text: time ? `Reprinted ${count}× · ${time}` : `Reprinted ${count}×`,
      tone: "warn",
      printCount: count,
      lastPrintedAt: last,
    };
  }
  if (hasPrint) {
    return {
      kind: "printed",
      text: time ? `Printed ${time}` : "Printed",
      tone: "warn",
      printCount: Math.max(count, 1),
      lastPrintedAt: last,
    };
  }
  if (o.status_group === "ready_to_ship" || o.status_group == null) {
    // null status_group: treat as candidate when caller already filtered RTS
    return {
      kind: "unprinted",
      text: "UNPRINTED",
      tone: "ok",
      printCount: 0,
      lastPrintedAt: null,
    };
  }
  return {
    kind: "none",
    text: "—",
    tone: "muted",
    printCount: 0,
    lastPrintedAt: null,
  };
}

export function isUnprintedOrder(o: PrintableOrderLike): boolean {
  const count = Number(o.print_count || 0);
  return !(Boolean(o.has_print) || count > 0);
}

export function partitionPrintSelection<T extends PrintableOrderLike>(
  orders: T[],
  selectedIds: Iterable<string>
): { selected: T[]; unprinted: T[]; printed: T[] } {
  const idSet = new Set(selectedIds);
  const selected = orders.filter((o) => idSet.has(o.id));
  const unprinted = selected.filter(isUnprintedOrder);
  const printed = selected.filter((o) => !isUnprintedOrder(o));
  return { selected, unprinted, printed };
}

export function countVisiblePrintState<T extends PrintableOrderLike>(orders: T[]) {
  let unprinted = 0;
  let printed = 0;
  for (const o of orders) {
    if (isUnprintedOrder(o)) unprinted += 1;
    else printed += 1;
  }
  return { unprinted, printed };
}

/**
 * Selection helpers — empty selection never means all.
 */
export function selectAllIds<T extends { id: string }>(orders: T[]): string[] {
  return orders.map((o) => o.id);
}

export function selectUnprintedIds<T extends PrintableOrderLike>(orders: T[]): string[] {
  return orders.filter(isUnprintedOrder).map((o) => o.id);
}
