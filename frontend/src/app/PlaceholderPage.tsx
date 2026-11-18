import { Layers } from 'lucide-react';
import { EmptyState } from '@/components/shared/EmptyState';
import { useTranslation } from '@/lib/i18n';

export function PlaceholderPage({ titleKey }: { titleKey: string }) {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-text-primary">{t(titleKey)}</h1>
      <EmptyState
        icon={<Layers className="h-10 w-10" />}
        title={t('placeholder.title')}
        description={t('placeholder.description')}
      />
    </div>
  );
}
