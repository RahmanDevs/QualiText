import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Database } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { DqiTrendChart, type DqiTrendPoint } from '@/components/shared/DqiTrendChart';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';
import { formatNumber, useTranslation } from '@/lib/i18n';

interface AttentionItem {
  dataset_id: string;
  dataset_name: string;
  version_id: string | null;
  version_number?: number;
  run_id?: string | null;
  dqi_total: number | null;
  critical_pii: number;
  invalid_row_count: number;
  reason: string;
}

interface WorkspaceOverviewData {
  workspace_id: string;
  total_datasets: number;
  avg_dqi: number | null;
  open_critical_pii: number;
  active_runs: number;
  datasets_needing_attention: AttentionItem[];
  dqi_trend: DqiTrendPoint[];
}

export function OverviewPage() {
  const { accessToken, activeWorkspaceId } = useAuthStore();
  const { t, locale } = useTranslation();
  const navigate = useNavigate();

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['workspace-overview', activeWorkspaceId],
    enabled: Boolean(activeWorkspaceId && accessToken),
    queryFn: () =>
      apiFetch<WorkspaceOverviewData>(
        `/workspaces/${activeWorkspaceId}/overview`,
        { method: 'GET' },
        accessToken,
      ),
  });

  function formatReason(item: AttentionItem): string {
    if (item.reason === 'critical_pii') {
      return t('overview.reasonCriticalPii', {
        count: formatNumber(item.critical_pii, locale),
      });
    }
    if (item.reason === 'low_dqi') return t('overview.reasonLowDqi');
    if (item.reason === 'unassessed') return t('overview.reasonUnassessed');
    if (item.reason === 'invalid_rows') {
      return t('overview.reasonInvalidRows', {
        count: formatNumber(item.invalid_row_count, locale),
      });
    }
    return t('overview.reasonNoUpload');
  }

  const totalDatasets = data?.total_datasets ?? 0;
  const avgDqi =
    data?.avg_dqi !== null && data?.avg_dqi !== undefined
      ? formatNumber(data.avg_dqi, locale)
      : '—';
  const openPii = data?.open_critical_pii ?? 0;
  const activeRuns = data?.active_runs ?? 0;

  const kpis = [
    {
      label: t('overview.kpiDatasets'),
      value: formatNumber(totalDatasets, locale),
    },
    {
      label: t('overview.kpiAvgDqi'),
      value: avgDqi,
    },
    {
      label: t('overview.kpiOpenPii'),
      value: formatNumber(openPii, locale),
    },
    {
      label: t('overview.kpiActiveRuns'),
      value: formatNumber(activeRuns, locale),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">{t('overview.title')}</h1>
          <p className="mt-1 text-sm text-text-secondary">{t('overview.subtitle')}</p>
        </div>
        <Badge tone="success">{t('overview.liveBadge')}</Badge>
      </div>

      {isLoading && <Skeleton className="h-32 w-full" />}
      {error && (
        <ErrorState
          message={error instanceof ApiError ? error.message : t('overview.loadError')}
          onRetry={() => refetch()}
        />
      )}

      {!isLoading && !error && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {kpis.map((kpi) => (
              <Card key={kpi.label} className="p-4">
                <p className="text-xs font-medium text-text-muted">{kpi.label}</p>
                <p className="mt-2 text-2xl font-bold tabular-nums text-text-primary">
                  {kpi.value}
                </p>
              </Card>
            ))}
          </div>

          {totalDatasets === 0 ? (
            <EmptyState
              icon={<Database className="h-10 w-10" />}
              title={t('overview.emptyTitle')}
              description={t('overview.emptyDescription')}
              actionLabel={t('overview.emptyAction')}
              onAction={() => navigate('/datasets')}
            />
          ) : (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <DqiTrendChart points={data?.dqi_trend ?? []} />

              <Card className="space-y-3 p-4">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 text-warning-600" aria-hidden="true" />
                  <h2 className="text-sm font-semibold text-text-primary">
                    {t('overview.attentionTitle')}
                  </h2>
                </div>

                {(data?.datasets_needing_attention.length ?? 0) === 0 ? (
                  <p className="text-xs text-text-muted">{t('overview.attentionEmpty')}</p>
                ) : (
                  <ul className="divide-y divide-border text-xs">
                    {data?.datasets_needing_attention.map((item) => (
                      <li
                        key={item.dataset_id}
                        className="flex items-center justify-between gap-3 py-2.5"
                      >
                        <div className="min-w-0">
                          <div className="truncate font-medium text-text-primary">
                            {item.dataset_name}
                          </div>
                          <div className="text-text-muted">{formatReason(item)}</div>
                        </div>
                        <Button
                          variant="secondary"
                          className="px-2.5 py-1 text-xs"
                          onClick={() =>
                            navigate(
                              item.run_id
                                ? `/runs?runId=${item.run_id}`
                                : item.version_id
                                  ? `/explorer?versionId=${item.version_id}`
                                  : '/datasets',
                            )
                          }
                        >
                          {t('overview.attentionOpen')}
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          )}
        </>
      )}
    </div>
  );
}
