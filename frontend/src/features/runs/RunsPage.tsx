import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, Download, GitCompare, ScatterChart } from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import {
  ClusterScatterChart,
  type ClusterPoint,
  type ClusterSummary,
} from '@/components/shared/ClusterScatterChart';
import { DimensionBreakdownChart } from '@/components/shared/DimensionBreakdownChart';
import { DqiTrendChart, type DqiTrendPoint } from '@/components/shared/DqiTrendChart';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { ScoreCard } from '@/components/shared/ScoreCard';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';
import { formatNumber, useTranslation } from '@/lib/i18n';

interface RunSummary {
  run_id: string;
  version_id: string;
  status: string;
  dqi_total: number | null;
  dqi_summary: {
    status_label?: string;
    is_partial?: boolean;
    critical_capped?: boolean;
    dimensions?: Array<{
      dimension: string;
      label: string;
      score: number;
      weight: number;
      contribution: number;
      findings_count: number;
    }>;
  };
  module_results: Array<{
    module: string;
    dimension: string;
    module_version: string;
    metrics: Record<string, number>;
    findings_count: number;
  }>;
}

interface DatasetItem {
  id: string;
  name: string;
}

interface TopicClustersResponse {
  version_id: string;
  entropy: number;
  clusters: ClusterSummary[];
  points: ClusterPoint[];
}

const TOKEN_HISTOGRAM_BUCKETS = [
  { key: 'hist_0_128', label: '0–128' },
  { key: 'hist_129_512', label: '129–512' },
  { key: 'hist_513_1024', label: '513–1024' },
  { key: 'hist_1025_2048', label: '1025–2048' },
  { key: 'hist_2049_4096', label: '2049–4096' },
  { key: 'hist_gt_4096', label: '> 4096' },
] as const;

export function RunsPage() {
  const { accessToken, activeWorkspaceId } = useAuthStore();
  const { t, locale } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const runId = searchParams.get('runId');
  const [downloading, setDownloading] = useState<'html' | 'pdf' | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [showClusters, setShowClusters] = useState(false);
  const [selectedDatasetId, setSelectedDatasetId] = useState<string>('');

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['run-summary', runId],
    enabled: Boolean(runId && accessToken),
    queryFn: () =>
      apiFetch<RunSummary>(`/runs/${runId}/summary`, { method: 'GET' }, accessToken),
  });

  const clustersQuery = useQuery({
    queryKey: ['topic-clusters', data?.version_id],
    enabled: Boolean(showClusters && data?.version_id && accessToken),
    queryFn: () =>
      apiFetch<TopicClustersResponse>(
        `/versions/${data!.version_id}/topic-clusters`,
        { method: 'GET' },
        accessToken,
      ),
  });

  const datasetsQuery = useQuery({
    queryKey: ['datasets-for-runs-trend', activeWorkspaceId],
    enabled: Boolean(!runId && activeWorkspaceId && accessToken),
    queryFn: () =>
      apiFetch<DatasetItem[]>(
        `/workspaces/${activeWorkspaceId}/datasets`,
        { method: 'GET' },
        accessToken,
      ),
  });

  const activeTrendDatasetId = selectedDatasetId || datasetsQuery.data?.[0]?.id || '';
  const trendQuery = useQuery({
    queryKey: ['dataset-dqi-trend', activeTrendDatasetId],
    enabled: Boolean(!runId && activeTrendDatasetId && accessToken),
    queryFn: () =>
      apiFetch<DqiTrendPoint[]>(
        `/datasets/${activeTrendDatasetId}/dqi-trend`,
        { method: 'GET' },
        accessToken,
      ),
  });

  async function handleDownloadReport(id: string, format: 'html' | 'pdf') {
    setDownloadError(null);
    setDownloading(format);
    try {
      const base = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1';
      const res = await fetch(`${base}/runs/${id}/report.${format}`, {
        method: 'GET',
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `qualitext_report_${id}.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch {
      setDownloadError(t('runs.downloadError'));
    } finally {
      setDownloading(null);
    }
  }

  if (!runId) {
    const datasets = datasetsQuery.data ?? [];
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold text-text-primary">{t('runs.title')}</h1>
        {datasets.length > 0 ? (
          <div className="space-y-4">
            <Card className="flex flex-wrap items-center gap-3 p-4">
              <label
                htmlFor="trend-dataset-select"
                className="text-xs font-medium text-text-secondary"
              >
                {t('runs.selectDatasetLabel')}
              </label>
              <select
                id="trend-dataset-select"
                value={activeTrendDatasetId}
                onChange={(e) => setSelectedDatasetId(e.target.value)}
                className="rounded-control border border-border bg-surface px-2.5 py-1.5 text-sm text-text-primary"
              >
                {datasets.map((ds) => (
                  <option key={ds.id} value={ds.id}>
                    {ds.name}
                  </option>
                ))}
              </select>
            </Card>
            <DqiTrendChart points={trendQuery.data ?? []} />
          </div>
        ) : null}

        <EmptyState
          icon={<Activity className="h-10 w-10" />}
          title={t('runs.emptyTitle')}
          description={t('runs.emptyDescription')}
        />
      </div>
    );
  }

  const tokenMod = data?.module_results.find((m) => m.module === 'token_analytics');
  const linguisticMod = data?.module_results.find((m) => m.module === 'linguistic_quality');
  const vlmMod = data?.module_results.find((m) => m.module === 'vlm_pdf_verifier');
  const hasHistogram = Boolean(tokenMod && 'hist_0_128' in tokenMod.metrics);
  const maxBucketVal = hasHistogram
    ? Math.max(1, ...TOKEN_HISTOGRAM_BUCKETS.map((b) => tokenMod!.metrics[b.key] ?? 0))
    : 1;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">{t('runs.title')}</h1>
          <p className="mt-1 font-mono text-xs text-text-muted">Run ID: {runId}</p>
        </div>
        {data && (
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="info">{data.status}</Badge>
            <Button
              variant="secondary"
              className="px-3 py-1.5 text-xs"
              disabled={downloading !== null}
              onClick={() => void handleDownloadReport(data.run_id, 'html')}
            >
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{downloading === 'html' ? t('runs.downloading') : t('runs.downloadHtml')}</span>
            </Button>
            <Button
              variant="secondary"
              className="px-3 py-1.5 text-xs"
              disabled={downloading !== null}
              onClick={() => void handleDownloadReport(data.run_id, 'pdf')}
            >
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{downloading === 'pdf' ? t('runs.downloading') : t('runs.downloadPdf')}</span>
            </Button>
            <Button
              variant="secondary"
              className="px-3 py-1.5 text-xs"
              onClick={() => navigate(`/dedup?versionId=${data.version_id}`)}
            >
              <GitCompare className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{t('runs.openDedupBtn')}</span>
            </Button>
            <Button
              variant="secondary"
              className="px-3 py-1.5 text-xs"
              onClick={() => setShowClusters((prev) => !prev)}
            >
              <ScatterChart className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{showClusters ? t('runs.hideClustersBtn') : t('runs.showClustersBtn')}</span>
            </Button>
          </div>
        )}
      </div>

      {downloadError && (
        <div
          role="alert"
          className="rounded-control border border-border bg-surface p-3 text-xs text-danger-600"
        >
          {downloadError}
        </div>
      )}

      {isLoading && <Skeleton className="h-48 w-full" />}
      {error && (
        <ErrorState
          message={error instanceof ApiError ? error.message : t('runs.loadError')}
          onRetry={() => refetch()}
        />
      )}

      {data && (
        <>
          <ScoreCard
            dqiTotal={data.dqi_total}
            statusLabel={data.dqi_summary.status_label}
            isPartial={data.dqi_summary.is_partial}
            criticalCapped={data.dqi_summary.critical_capped}
            dimensions={data.dqi_summary.dimensions}
            onDimensionClick={() => navigate(`/explorer?versionId=${data.version_id}`)}
          />

          <DimensionBreakdownChart
            dimensions={data.dqi_summary.dimensions ?? []}
            onDimensionClick={() => navigate(`/explorer?versionId=${data.version_id}`)}
          />

          {showClusters && (
            <>
              {clustersQuery.isLoading && <Skeleton className="h-64 w-full" />}
              {clustersQuery.data && (
                <ClusterScatterChart
                  clusters={clustersQuery.data.clusters}
                  points={clustersQuery.data.points}
                  entropy={clustersQuery.data.entropy}
                />
              )}
            </>
          )}

          {hasHistogram && tokenMod && (
            <Card className="space-y-3 p-4">
              <h2 className="text-sm font-semibold text-text-primary">
                {t('runs.tokenHistogramTitle')}
              </h2>
              <div className="space-y-2">
                {TOKEN_HISTOGRAM_BUCKETS.map((bucket) => {
                  const val = tokenMod.metrics[bucket.key] ?? 0;
                  const pct = Math.round((val / maxBucketVal) * 100);
                  return (
                    <div key={bucket.key} className="flex items-center gap-3 text-xs">
                      <span className="w-24 font-mono text-text-secondary">{bucket.label}</span>
                      <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-subtle">
                        <div
                          className="h-full rounded-full bg-brand-600"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      <span className="w-20 text-right tabular-nums text-text-primary">
                        {t('runs.rowsUnit', { count: formatNumber(val, locale) })}
                      </span>
                    </div>
                  );
                })}
              </div>
            </Card>
          )}

          {linguisticMod && (
            <Card className="space-y-3 p-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-text-primary">
                  {t('runs.linguisticCardTitle')}
                </h2>
                <Badge tone="info">
                  {t('runs.findingsCount', {
                    count: formatNumber(linguisticMod.findings_count, locale),
                  })}
                </Badge>
              </div>
              <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-5">
                <div>
                  <dt className="text-text-muted">{t('runs.avgPerplexity')}</dt>
                  <dd className="font-mono font-semibold tabular-nums text-text-primary">
                    {linguisticMod.metrics.avg_perplexity ?? 0}
                  </dd>
                </div>
                <div>
                  <dt className="text-text-muted">{t('runs.p95Perplexity')}</dt>
                  <dd className="font-mono font-semibold tabular-nums text-text-primary">
                    {linguisticMod.metrics.p95_perplexity ?? 0}
                  </dd>
                </div>
                <div>
                  <dt className="text-text-muted">{t('runs.validWordRatio')}</dt>
                  <dd className="font-mono font-semibold tabular-nums text-success-600">
                    {Math.round((linguisticMod.metrics.valid_word_ratio ?? 1) * 100)}%
                  </dd>
                </div>
                <div>
                  <dt className="text-text-muted">{t('runs.oovRatio')}</dt>
                  <dd className="font-mono font-semibold tabular-nums text-warning-600">
                    {Math.round((linguisticMod.metrics.oov_ratio ?? 0) * 100)}%
                  </dd>
                </div>
                <div>
                  <dt className="text-text-muted">{t('runs.gibberishRows')}</dt>
                  <dd className="font-mono font-semibold tabular-nums text-text-primary">
                    {linguisticMod.metrics.gibberish_rows ?? 0}
                  </dd>
                </div>
              </dl>
              <div className="h-2.5 w-full overflow-hidden rounded-full bg-subtle">
                <div
                  className="h-full rounded-full bg-success-600"
                  style={{
                    width: `${Math.min(100, Math.round((linguisticMod.metrics.valid_word_ratio ?? 1) * 100))}%`,
                  }}
                />
              </div>
            </Card>
          )}

          {vlmMod && (
            <Card className="space-y-3 p-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-text-primary">
                  {t('runs.vlmCardTitle')}
                </h2>
                <Badge tone="info">
                  {t('runs.findingsCount', {
                    count: formatNumber(vlmMod.findings_count, locale),
                  })}
                </Badge>
              </div>
              <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
                <div>
                  <dt className="text-text-muted">{t('runs.vlmPagesVerified')}</dt>
                  <dd className="font-mono font-semibold tabular-nums text-text-primary">
                    {vlmMod.metrics.verified_pages ?? 0} / {vlmMod.metrics.total_pages ?? 0}
                  </dd>
                </div>
                <div>
                  <dt className="text-text-muted">{t('runs.vlmAvgAccuracy')}</dt>
                  <dd className="font-mono font-semibold tabular-nums text-success-600">
                    {Math.round((vlmMod.metrics.avg_vlm_accuracy_score ?? 1) * 100)}%
                  </dd>
                </div>
                <div>
                  <dt className="text-text-muted">{t('runs.vlmTableFidelity')}</dt>
                  <dd className="font-mono font-semibold tabular-nums text-info-600">
                    {Math.round((vlmMod.metrics.avg_table_fidelity_score ?? 1) * 100)}%
                  </dd>
                </div>
                <div>
                  <dt className="text-text-muted">{t('runs.vlmFlaggedPages')}</dt>
                  <dd className="font-mono font-semibold tabular-nums text-danger-600">
                    {vlmMod.metrics.flagged_pages ?? 0}
                  </dd>
                </div>
              </dl>
            </Card>
          )}

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {data.module_results.map((mr) => (
              <Card key={mr.module} className="space-y-2 p-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-text-primary">{mr.module}</h2>
                  <Badge>
                    {t('runs.findingsCount', {
                      count: formatNumber(mr.findings_count, locale),
                    })}
                  </Badge>
                </div>
                <dl className="grid grid-cols-2 gap-2 pt-2 text-xs">
                  {Object.entries(mr.metrics).map(([k, v]) => (
                    <div key={k}>
                      <dt className="text-text-muted">{k}</dt>
                      <dd className="font-mono font-medium tabular-nums text-text-primary">{v}</dd>
                    </div>
                  ))}
                </dl>
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
