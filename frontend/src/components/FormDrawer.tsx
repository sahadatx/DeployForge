import { useEffect, useId, useRef, type ReactNode } from "react";
import { RotateCcw, X } from "lucide-react";

export function FieldLabel({ children, required = false, optional = false }: { children: ReactNode; required?: boolean; optional?: boolean }) {
  return <span className="field-label">{children}{required && <span className="field-required" aria-hidden="true"> *</span>}{optional && <span className="field-optional"> optional</span>}</span>;
}

type FormDrawerProps = {
  title: string;
  description?: string;
  eyebrow?: string;
  children: ReactNode;
  onClose: () => void;
  dirty?: boolean;
  onReset?: () => void;
  busy?: boolean;
  error?: string;
  actions?: ReactNode;
  className?: string;
  readOnly?: boolean;
};

/** A modal drawer that preserves unfinished form input until it is deliberately discarded. */
export function FormDrawer({
  title,
  description,
  eyebrow = "WORKSPACE FORM",
  children,
  onClose,
  dirty = false,
  onReset,
  busy = false,
  error,
  actions,
  className = "",
  readOnly = false,
}: FormDrawerProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const feedbackRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.showModal();
    const firstInput = dialog.querySelector<HTMLElement>(".form-drawer-body [data-initial-focus], .form-drawer-body [autofocus]")
      || dialog.querySelector<HTMLElement>(".form-drawer-body input:not([disabled]):not([type='hidden']), .form-drawer-body select:not([disabled]), .form-drawer-body textarea:not([disabled])");
    const focusTimer = window.setTimeout(() => {
      firstInput?.focus({ preventScroll: true });
    }, 0);
    return () => {
      window.clearTimeout(focusTimer);
      dialog.close();
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, []);

  useEffect(() => {
    if (error) feedbackRef.current?.focus();
  }, [error]);

  function discardAllowed() {
    return !dirty || window.confirm(`Discard unsaved changes to “${title}”?`);
  }

  function close() {
    if (!busy && discardAllowed()) {
      dialogRef.current?.close();
      onClose();
    }
  }

  function reset() {
    if (!busy && discardAllowed()) {
      dialogRef.current?.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input, textarea").forEach((control) => control.setCustomValidity(""));
      onReset?.();
    }
  }

  return <dialog
    ref={dialogRef}
    className={`form-drawer ${className}`.trim()}
    aria-labelledby={titleId}
    aria-describedby={description ? descriptionId : undefined}
    aria-busy={busy}
    onInputCapture={(event) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) event.target.setCustomValidity("");
    }}
    onSubmitCapture={(event) => {
      if (!(event.target instanceof HTMLFormElement)) return;
      const controls = event.target.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input[required], textarea[required]");
      for (const control of controls) {
        if (control.disabled || (control instanceof HTMLInputElement && ["checkbox", "radio", "number", "hidden"].includes(control.type))) continue;
        const minimum = Math.max(1, control.minLength);
        if (control.value.trim().length >= minimum) continue;
        const labelElement = control.labels?.[0];
        const label = (labelElement?.querySelector(".field-label")?.textContent || labelElement?.firstChild?.textContent)?.replace(/\*/g, "").trim() || "this field";
        control.setCustomValidity(minimum === 1 ? `Enter a value for ${label}.` : `Enter at least ${minimum} characters for ${label}.`);
        event.preventDefault();
        event.stopPropagation();
        control.reportValidity();
        break;
      }
    }}
    onCancel={(event) => { event.preventDefault(); close(); }}
    onClick={(event) => {
      if (event.target !== event.currentTarget) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) close();
    }}
  >
    <header className="form-drawer-header">
      <div className="form-drawer-heading"><span className="eyebrow">{eyebrow}</span><h2 id={titleId}>{title}</h2>{description && <p id={descriptionId}>{description}</p>}</div>
      <button className="icon-button form-drawer-dismiss" type="button" disabled={busy} onClick={close} aria-label={`Close ${title}`}><X size={18} /></button>
    </header>
    <div className="form-drawer-body">
      {!readOnly && <p className="form-required-legend"><span className="field-required" aria-hidden="true">*</span> Required fields</p>}
      {error && <div ref={feedbackRef} className="settings-notice is-error form-drawer-feedback" role="alert" tabIndex={-1}>{error}</div>}
      <fieldset className="form-drawer-fieldset" disabled={busy}>{children}</fieldset>
    </div>
    <footer className="form-drawer-footer">
      <div className="form-drawer-secondary-actions">{onReset && !readOnly && <button className="catalog-secondary-button" type="button" disabled={busy || !dirty} onClick={reset}><RotateCcw size={14} />Reset</button>}<button className="catalog-secondary-button" type="button" disabled={busy} onClick={close}>{readOnly ? "Close" : "Cancel"}</button></div>
      {actions && <div className="form-drawer-primary-actions">{actions}</div>}
      {busy && <span className="sr-only" role="status">Saving changes. Please wait.</span>}
    </footer>
  </dialog>;
}
