import type { ButtonHTMLAttributes } from 'react';

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'destructive';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: 'bg-brand-600 text-white hover:opacity-95 focus-visible:ring-2 focus-visible:ring-brand-600',
  secondary:
    'bg-surface text-text-primary border border-border hover:bg-subtle focus-visible:ring-2 focus-visible:ring-brand-600',
  ghost: 'bg-transparent text-text-secondary hover:bg-subtle hover:text-text-primary',
  destructive: 'bg-danger-600 text-white hover:opacity-95 focus-visible:ring-2 focus-visible:ring-danger-600',
};

export function Button({
  variant = 'primary',
  className = '',
  type = 'button',
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={`inline-flex items-center justify-center gap-2 rounded-control px-4 py-2 text-sm font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none ${VARIANT_CLASSES[variant]} ${className}`}
      {...props}
    />
  );
}
