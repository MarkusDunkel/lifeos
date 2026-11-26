import { Loader2, type LucideIcon } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

const BASE =
  "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap transition-colors disabled:pointer-events-none disabled:opacity-45";

const SIZE: Record<ButtonSize, string> = {
  sm: "px-2.5 py-1 text-xs",
  md: "px-3 py-1.5 text-sm",
};

const VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-accent-600 text-white shadow-sm hover:bg-accent-700",
  secondary:
    "border border-slate-300 bg-white text-slate-700 shadow-sm hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800",
  ghost: "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
  danger:
    "border border-red-300 bg-white text-red-700 shadow-sm hover:bg-red-50 dark:border-red-800 dark:bg-slate-900 dark:text-red-300 dark:hover:bg-red-950/40",
};

/** Shape and size of a button without a colour, for the few places that colour it themselves. */
export const buttonBase = `${BASE} ${SIZE.md}`;

/** The classes of a button, for links and labels that should look like one. */
export function buttonClass(variant: ButtonVariant = "secondary", size: ButtonSize = "md", extra = ""): string {
  return `${BASE} ${SIZE[size]} ${VARIANT[variant]} ${extra}`;
}

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: LucideIcon;
  /** Shows a spinner in place of the icon and disables the button. */
  loading?: boolean;
  /** Keeps the icon and spins it while loading (e.g. a refresh arrow). */
  spinIcon?: boolean;
  children?: ReactNode;
};

export function Button({
  variant = "secondary",
  size = "md",
  icon: Icon,
  loading = false,
  spinIcon = false,
  className = "",
  children,
  disabled,
  type = "button",
  ...rest
}: Props) {
  const iconSize = size === "sm" ? "size-3.5" : "size-4";
  return (
    <button type={type} className={buttonClass(variant, size, className)} disabled={disabled || loading} {...rest}>
      {loading && !(spinIcon && Icon) ? (
        <Loader2 className={`${iconSize} animate-spin`} />
      ) : (
        Icon && <Icon className={`${iconSize} ${loading ? "animate-spin" : ""}`} />
      )}
      {children}
    </button>
  );
}

/** A square button that shows only an icon; ``label`` is its accessible name and tooltip. */
export function IconButton({
  icon: Icon,
  label,
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: LucideIcon; label: string }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      className={`inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800 disabled:opacity-45 dark:hover:bg-slate-800 dark:hover:text-slate-100 ${className}`}
      {...rest}
    >
      <Icon className="size-4.5" />
    </button>
  );
}
