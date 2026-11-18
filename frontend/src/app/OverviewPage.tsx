import { Database } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { EmptyState } from '@/components/shared/EmptyState';
import { formatNumber, useTranslation } from '@/lib/i18n';

export function OverviewPage() {
  const { t, locale } = useTranslation();

  const kpis = [
    { label: t('overview.kpiDatasets'), value: formatNumber(0, locale), tone: 'neutral' as const },
    { label: t('overview.kpiAvgDqi'), value: '—', tone: 'info' as const },
    { label: t('overview.kpiOpenPii'), value: formatNumber(0, locale), tone: 'success' as const },
    { label: t('overview.kpiActiveRuns'), value: formatNumber(0, locale), tone: 'neutral' as const },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">{t('overview.title')}</h1>
          <p className="mt-1 text-sm text-text-secondary">{t('overview.subtitle')}</p>
        </div>
        <Badge tone="info">Fase 0 — Fondasi</Badge>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {kpis.map((kpi) => (
          <Card key={kpi.label} className="p-4">
            <p className="text-xs font-medium text-text-muted">{kpi.label}</p>
            <p className="mt-2 text-2xl font-bold tabular-nums text-text-primary">{kpi.value}</p>
          </Card>
        ))}
      </div>

      <EmptyState
        icon={<Database className="h-10 w-10" />}
        title={t('overview.emptyTitle')}
        description={t('overview.emptyDescription')}
      />
    </div>
  );
}
