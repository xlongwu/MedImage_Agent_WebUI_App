import {
  forwardRef,
  useId,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type HTMLAttributes,
  type ReactNode,
} from "react";

import styles from "./primitives.module.css";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md";
type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger";
type CardTone = "default" | "muted" | "elevated";

function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

const buttonVariantClass: Record<ButtonVariant, string> = {
  primary: styles.variantPrimary,
  secondary: styles.variantSecondary,
  ghost: styles.variantGhost,
  danger: styles.variantDanger,
};

const buttonSizeClass: Record<ButtonSize, string> = {
  sm: styles.sizeSm,
  md: styles.sizeMd,
};

const badgeToneClass: Record<BadgeTone, string> = {
  neutral: styles.badgeNeutral,
  info: styles.badgeInfo,
  success: styles.badgeSuccess,
  warning: styles.badgeWarning,
  danger: styles.badgeDanger,
};

const cardToneClass: Record<CardTone, string> = {
  default: styles.cardDefault,
  muted: styles.cardMuted,
  elevated: styles.cardElevated,
};

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;
  fullWidth?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    children,
    className,
    fullWidth = false,
    leadingIcon,
    size = "md",
    trailingIcon,
    type = "button",
    variant = "secondary",
    ...props
  },
  ref,
) {
  return (
    <button
      {...props}
      ref={ref}
      type={type}
      className={cx(
        styles.button,
        buttonVariantClass[variant],
        buttonSizeClass[size],
        fullWidth && styles.fullWidth,
        className,
      )}
    >
      {leadingIcon ? <span className={styles.iconSlot}>{leadingIcon}</span> : null}
      {children ? <span className={styles.buttonLabel}>{children}</span> : null}
      {trailingIcon ? <span className={styles.iconSlot}>{trailingIcon}</span> : null}
    </button>
  );
});

export type IconButtonProps = Omit<ButtonProps, "children" | "leadingIcon" | "trailingIcon"> & {
  children: ReactNode;
  label: string;
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { children, className, label, title, variant = "ghost", ...props },
  ref,
) {
  return (
    <Button
      {...props}
      ref={ref}
      aria-label={label}
      className={cx(styles.iconButton, className)}
      title={title ?? label}
      variant={variant}
    >
      <span className={styles.iconOnly}>{children}</span>
    </Button>
  );
});

export type BadgeProps = HTMLAttributes<HTMLSpanElement> & {
  tone?: BadgeTone;
  size?: ButtonSize;
};

export function Badge({ className, size = "md", tone = "neutral", ...props }: BadgeProps) {
  return (
    <span
      {...props}
      className={cx(styles.badge, badgeToneClass[tone], buttonSizeClass[size], className)}
    />
  );
}

export type CardProps = HTMLAttributes<HTMLDivElement> & {
  tone?: CardTone;
};

export function Card({ className, tone = "default", ...props }: CardProps) {
  return <div {...props} className={cx(styles.card, cardToneClass[tone], className)} />;
}

export type EmptyStateProps = HTMLAttributes<HTMLDivElement> & {
  action?: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  title: ReactNode;
};

export function EmptyState({
  action,
  className,
  description,
  icon,
  title,
  ...props
}: EmptyStateProps) {
  return (
    <div {...props} className={cx(styles.emptyState, className)}>
      {icon ? <div className={styles.emptyStateIcon}>{icon}</div> : null}
      <div className={styles.emptyStateText}>
        <strong>{title}</strong>
        {description ? <p>{description}</p> : null}
      </div>
      {action ? <div className={styles.emptyStateAction}>{action}</div> : null}
    </div>
  );
}

export type SkeletonProps = HTMLAttributes<HTMLSpanElement> & {
  height?: CSSProperties["height"];
  width?: CSSProperties["width"];
};

export function Skeleton({
  "aria-hidden": ariaHidden = true,
  className,
  height = 16,
  style,
  width = "100%",
  ...props
}: SkeletonProps) {
  return (
    <span
      {...props}
      aria-hidden={ariaHidden}
      className={cx(styles.skeleton, className)}
      style={{ width, height, ...style }}
    />
  );
}

export type ProgressProps = HTMLAttributes<HTMLDivElement> & {
  label: ReactNode;
  value: number | null;
};

export function Progress({ className, label, value, ...props }: ProgressProps) {
  const normalized = value == null ? null : Math.min(100, Math.max(0, value));

  return (
    <div
      {...props}
      aria-label={typeof label === "string" ? label : undefined}
      aria-valuemax={100}
      aria-valuemin={0}
      aria-valuenow={normalized ?? undefined}
      aria-valuetext={normalized == null ? "Unavailable" : `${normalized}%`}
      className={cx(styles.progress, className)}
      role="progressbar"
    >
      <span className={styles.progressLabel}>{label}</span>
      <span className={styles.progressTrack}>
        {normalized == null ? (
          <span aria-hidden="true" className={styles.progressIndeterminate} />
        ) : (
          <span className={styles.progressValue} style={{ width: `${normalized}%` }} />
        )}
      </span>
      <span className={styles.progressText}>{normalized == null ? "—" : `${normalized}%`}</span>
    </div>
  );
}

export type TooltipProps = HTMLAttributes<HTMLSpanElement> & {
  label: ReactNode;
  placement?: "top" | "bottom";
};

export function Tooltip({ children, className, label, placement = "top", ...props }: TooltipProps) {
  const tooltipId = useId();

  return (
    <span {...props} className={cx(styles.tooltipRoot, className)}>
      <span aria-describedby={tooltipId} className={styles.tooltipAnchor}>
        {children}
      </span>
      <span
        id={tooltipId}
        role="tooltip"
        className={cx(
          styles.tooltipBubble,
          placement === "bottom" ? styles.tooltipBottom : styles.tooltipTop,
        )}
      >
        {label}
      </span>
    </span>
  );
}
