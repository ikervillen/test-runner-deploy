import { useEffect, type ReactNode } from "react";

const LAYERS = {
  primary: {
    overlay: "z-50 bg-slate-900/35 py-8",
    panel: "max-h-[90vh] max-w-6xl shadow-xl",
  },
  nested: {
    overlay: "z-[60] bg-slate-950/45 py-10",
    panel: "max-h-[88vh] max-w-[96vw] shadow-2xl",
  },
} as const;

/**
 * Full-screen overlay with a sticky header; Escape closes it.
 * `nested` stacks above `primary`.
 */
export function Modal({
  layer,
  title,
  onClose,
  children,
}: {
  layer: keyof typeof LAYERS;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const classes = LAYERS[layer];
  return (
    <div
      className={`fixed inset-0 flex items-start justify-center px-4 ${classes.overlay}`}
    >
      <section
        role="dialog"
        aria-modal="true"
        className={`w-full overflow-y-auto rounded-lg bg-white ${classes.panel}`}
      >
        <header className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-gray-200 bg-white px-5 py-4">
          {title}
        </header>
        {children}
      </section>
    </div>
  );
}

export function CloseButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50"
    >
      Cerrar
    </button>
  );
}
