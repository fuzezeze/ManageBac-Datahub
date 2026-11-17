import { useEffect, type ReactNode } from "react";
import { absoluteTime, relativeTime, type PillTone } from "./format";

export function Pill({ tone, children }: { tone: PillTone; children: ReactNode }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function PageHead({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
      </div>
      {children ? <div className="page-actions">{children}</div> : null}
    </header>
  );
}

export function ErrorBanner({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  if (!message) return null;
  return (
    <div className="banner danger" role="alert">
      <span>{message}</span>
      {onDismiss ? (
        <button type="button" className="ghost" onClick={onDismiss}>
          Dismiss
        </button>
      ) : null}
    </div>
  );
}

export function Time({ iso, zone }: { iso: string | null | undefined; zone: string }) {
  if (!iso) return <>—</>;
  return (
    <time dateTime={iso} title={absoluteTime(iso, zone)}>
      {relativeTime(iso)}
    </time>
  );
}

export function When({ iso, zone }: { iso: string | null | undefined; zone: string }) {
  if (!iso) return <>—</>;
  return (
    <span className="when">
      <Time iso={iso} zone={zone} />
      <span>{absoluteTime(iso, zone)}</span>
    </span>
  );
}

export function Dialog({
  title,
  children,
  onClose,
  wide,
}: {
  title: string;
  children: ReactNode;
  onClose?: () => void;
  wide?: boolean;
}) {
  useEffect(() => {
    if (!onClose) return;
    const close = onClose;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className={wide ? "drawer-backdrop" : "modal-backdrop"} onMouseDown={onClose}>
      <div
        className={wide ? "drawer" : "modal"}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-head">
          <h2>{title}</h2>
          {onClose ? (
            <button type="button" className="ghost" onClick={onClose}>
              Close
            </button>
          ) : null}
        </div>
        {children}
      </div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}
