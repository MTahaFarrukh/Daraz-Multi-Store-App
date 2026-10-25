import { useEffect, type ReactNode } from "react";

export function Dialog({
  open,
  title,
  subtitle,
  children,
  onClose,
  footer,
  size = "md",
  closeOnBackdrop = true,
  closeDisabled = false,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  size?: "md" | "lg" | "drawer";
  closeOnBackdrop?: boolean;
  closeDisabled?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !closeDisabled) onClose();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, closeDisabled, onClose]);

  if (!open) return null;

  function handleBackdrop() {
    if (!closeOnBackdrop || closeDisabled) return;
    onClose();
  }

  return (
    <div className="dialog-backdrop" role="presentation" onClick={handleBackdrop}>
      <div
        className={`dialog-panel dialog-panel--${size}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dialog-header">
          <div className="dialog-header-text">
            <h3>{title}</h3>
            {subtitle ? <p className="dialog-subtitle">{subtitle}</p> : null}
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onClose}
            disabled={closeDisabled}
            aria-label="Close"
          >
            Close
          </button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer ? <div className="dialog-footer">{footer}</div> : null}
      </div>
    </div>
  );
}
