"use client";

import Link from "next/link";
import { useEffect, useId, useRef, type ReactNode } from "react";
import { messages } from "@/lib/messages";

const buttonBase =
  "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50";
const variants = {
  primary: "bg-accent text-accent-fg hover:brightness-110",
  secondary: "border border-line bg-surface text-fg hover:bg-surface-2",
  danger: "border border-fail/40 text-fail hover:bg-fail-soft",
  ghost: "text-muted hover:bg-surface-2 hover:text-fg",
} as const;
export type ButtonVariant = keyof typeof variants;
const buttonClass = (variant: ButtonVariant, extra = "") =>
  `${buttonBase} ${variants[variant]} ${extra}`;

export function Button({
  variant = "secondary",
  className,
  type = "button",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return <button type={type} className={buttonClass(variant, className)} {...props} />;
}

export function LinkButton({
  href,
  variant = "secondary",
  children,
}: {
  href: string;
  variant?: ButtonVariant;
  children: ReactNode;
}) {
  return (
    <Link href={href} className={buttonClass(variant)}>
      {children}
    </Link>
  );
}

/** A small square button that holds only an icon. Always give it a label. */
export function IconButton({
  label,
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`grid size-9 place-items-center rounded-lg text-muted transition-colors hover:bg-surface-2 hover:text-fg disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
      {...props}
    />
  );
}

/** Like IconButton, but a link: for actions that open another page. */
export function IconLink({
  href,
  label,
  className = "",
  children,
}: {
  href: string;
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Link
      href={href}
      aria-label={label}
      title={label}
      className={`grid size-9 place-items-center rounded-lg text-muted transition-colors hover:bg-surface-2 hover:text-fg ${className}`}
    >
      {children}
    </Link>
  );
}

export const inputClass =
  "w-full rounded-lg border border-line bg-surface px-3.5 py-2.5 text-sm placeholder:text-muted/70 focus:border-accent aria-[invalid=true]:border-fail";

/** For a select that sits next to others instead of filling its row. */
export const selectClass =
  "rounded-lg border border-line bg-surface px-3.5 py-2.5 text-sm focus:border-accent";

/** The white rounded surface everything sits on. */
export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-xl border border-line bg-surface shadow-card ${className}`}>
      {children}
    </div>
  );
}

const PILL_TONES = {
  ok: "bg-ok-soft text-ok",
  wait: "bg-wait-soft text-wait",
  fail: "bg-fail-soft text-fail",
  unconf: "bg-unconf-soft text-unconf",
  accent: "bg-accent-soft text-accent",
  muted: "bg-surface-2 text-muted",
} as const;
export type PillTone = keyof typeof PILL_TONES;

export function Pill({
  tone = "muted",
  dot = false,
  children,
}: {
  tone?: PillTone;
  dot?: boolean;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-semibold ${PILL_TONES[tone]}`}
    >
      {dot ? <span aria-hidden className="size-1.5 rounded-full bg-current" /> : null}
      {children}
    </span>
  );
}

/**
 * A choice shown as a grey card with its control on the left, like the trigger options of a
 * ManyChat flow. The content under the title (a picker, a field) only shows while it is chosen.
 */
export function OptionCard({
  type,
  name,
  checked,
  onChange,
  title,
  description,
  nested = false,
  children,
}: {
  /** Inside another card the grey would vanish, so a nested card is white with a border. */
  nested?: boolean;
  type: "radio" | "checkbox";
  name?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  title: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <div
      className={`rounded-xl p-4 transition-shadow ${
        nested ? "border border-line bg-surface" : "bg-surface-2"
      } ${checked ? "ring-1 ring-accent/60" : ""}`}
    >
      <label className="flex cursor-pointer items-start gap-3">
        <input
          type={type}
          name={name}
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          className="mt-0.5 size-4"
        />
        <span className="text-sm">
          <span className="font-semibold">{title}</span>
          {description ? <span className="mt-0.5 block text-muted">{description}</span> : null}
        </span>
      </label>
      {checked && children ? <div className="mt-4">{children}</div> : null}
    </div>
  );
}

/** A grey card with a switch. What is under the title only shows while the switch is on. */
export function ToggleCard({
  title,
  description,
  checked,
  onChange,
  children,
}: {
  title: string;
  description?: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-xl bg-surface-2 p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="text-sm">
          <p className="font-semibold">{title}</p>
          {description ? <p className="mt-0.5 text-muted">{description}</p> : null}
        </div>
        <Switch checked={checked} onChange={onChange} label={title} />
      </div>
      {checked && children ? <div className="mt-4 space-y-4">{children}</div> : null}
    </div>
  );
}

/** Label, hint and error wired to the control through ids. Pass the control as a function. */
export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: (props: { id: string; "aria-describedby": string; "aria-invalid": boolean }) => ReactNode;
}) {
  const id = useId();
  const describedBy = `${id}-d`;
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-semibold">
        {label}
      </label>
      <div className="mt-1.5">
        {children({ id, "aria-describedby": describedBy, "aria-invalid": Boolean(error) })}
      </div>
      <div id={describedBy}>
        {error ? (
          <p className="mt-1.5 text-sm text-fail">{error}</p>
        ) : hint ? (
          <p className="mt-1.5 text-sm text-muted">{hint}</p>
        ) : null}
      </div>
    </div>
  );
}

export function PageHeader({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** One live region per page keeps announcements out of the layout. */
export function Notice({
  kind,
  children,
}: {
  kind: "ok" | "error";
  children: ReactNode;
}) {
  return (
    <p
      role={kind === "error" ? "alert" : "status"}
      className={`rounded-lg px-4 py-3 text-sm font-medium ${
        kind === "error" ? "bg-fail-soft text-fail" : "bg-ok-soft text-ok"
      }`}
    >
      {children}
    </p>
  );
}

const STATE_TONE: Record<string, PillTone> = {
  SENT: "ok",
  PROCESSED: "ok",
  PENDING: "wait",
  FAILED: "fail",
  UNCONFIRMED: "unconf",
};

/** The label says the state; the colour only backs it up. */
export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const text = label ?? messages.deliveries.status[status] ?? status;
  return (
    <Pill tone={STATE_TONE[status] ?? "muted"} dot>
      {text}
    </Pill>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-50 ${
        checked ? "bg-accent" : "bg-line"
      }`}
    >
      <span
        aria-hidden
        className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-all ${
          checked ? "left-[1.375rem]" : "left-0.5"
        }`}
      />
    </button>
  );
}

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  confirmVariant = "danger",
  confirmDisabled,
  pending,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  confirmVariant?: ButtonVariant;
  confirmDisabled?: boolean;
  pending?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
      className="m-auto w-[min(92vw,28rem)] rounded-xl border border-line bg-surface p-6 text-fg shadow-card"
    >
      <h2 id={titleId} className="text-lg font-bold">
        {title}
      </h2>
      <div className="mt-2 space-y-3 text-sm text-muted">{children}</div>
      <div className="mt-6 flex justify-end gap-2">
        <Button onClick={onCancel}>{messages.common.cancel}</Button>
        <Button variant={confirmVariant} onClick={onConfirm} disabled={confirmDisabled || pending}>
          {confirmLabel}
        </Button>
      </div>
    </dialog>
  );
}
