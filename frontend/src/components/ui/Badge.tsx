import type { HTMLAttributes } from 'react';

type BadgeTone = 'neutral' | 'success' | 'warning' | 'danger' | 'info';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral: 'bg-subtle text-text-secondary border-border',
  success: 'bg-subtle text-success-600 border-border',
  warning: 'bg-subtle text-warning-600 border-border',
  danger: 'bg-subtle text-danger-600 border-border',
  info: 'bg-brand-50 text-brand-600 border-border',
};

export function Badge({ tone = 'neutral', className = '', ...props }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-control border px-2 py-0.5 text-xs font-medium ${TONE_CLASSES[tone]} ${className}`}
      {...props}
    />
  );
}
