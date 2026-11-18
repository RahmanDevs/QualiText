import type { InputHTMLAttributes } from 'react';

export function Input({ className = '', ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={`w-full rounded-control border border-border bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-50 ${className}`}
      {...props}
    />
  );
}
