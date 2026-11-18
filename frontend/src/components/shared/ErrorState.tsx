import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useTranslation } from '@/lib/i18n';

interface ErrorStateProps {
  message: string;
  onRetry?: () => void;
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  const { t } = useTranslation();

  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center rounded-card border border-border bg-surface p-8 text-center"
    >
      <AlertTriangle className="mb-3 h-8 w-8 text-danger-600" aria-hidden="true" />
      <h3 className="text-base font-semibold text-text-primary">{t('states.errorTitle')}</h3>
      <p className="mt-1 max-w-md text-sm text-text-secondary">{message}</p>
      {onRetry && (
        <Button variant="secondary" className="mt-4" onClick={onRetry}>
          {t('states.retry')}
        </Button>
      )}
    </div>
  );
}
