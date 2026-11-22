import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { GitCompare } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';
import { formatNumber, useTranslation } from '@/lib/i18n';

interface DedupMember {
  row_id: string;
  similarity: number;
  is_canonical: boolean;
  char_len: number;
  text_preview: string;
}

interface DedupGroup {
  group_id: string;
  canonical_row_id: string;
  avg_similarity: number;
  members: DedupMember[];
}

interface DedupPreviewResponse {
  version_id: string;
  threshold: number;
  scanned_rows: number;
  duplicate_groups_count: number;
  duplicate_rows_count: number;
  embedding_backend: string;
  groups: DedupGroup[];
}

interface DatasetWithLatest {
  id: string;
  name: string;
  latest_version: { id: string; version_number: number } | null;
}

export function DedupReviewPage() {
  const { accessToken, activeWorkspaceId } = useAuthStore();
  const { t, locale } = useTranslation();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const versionId = searchParams.get('versionId');
  const [threshold, setThreshold] = useState(0.88);
  const [banner, setBanner] = useState<string | null>(null);
  const [errBanner, setErrBanner] = useState<string | null>(null);

  const datasetsQuery = useQuery({
    queryKey: ['datasets-for-dedup', activeWorkspaceId],
    enabled: Boolean(!versionId && activeWorkspaceId && accessToken),
    queryFn: () =>
      apiFetch<DatasetWithLatest[]>(
        `/workspaces/${activeWorkspaceId}/datasets`,
        { method: 'GET' },
        accessToken,
      ),
  });

  const activeVersionId =
    versionId || datasetsQuery.data?.find((d) => d.latest_version !== null)?.latest_version?.id || '';

  const previewQuery = useQuery({
    queryKey: ['dedup-preview', activeVersionId, threshold],
    enabled: Boolean(activeVersionId && accessToken),
    queryFn: () =>
      apiFetch<DedupPreviewResponse>(
        `/versions/${activeVersionId}/dedup-preview?threshold=${threshold.toFixed(2)}`,
        { method: 'GET' },
        accessToken,
      ),
  });

  const curateMutation = useMutation({
    mutationFn: (action: 'reject' | 'drop_row') => {
      const nonCanonIds = (previewQuery.data?.groups ?? []).flatMap((g) =>
        g.members.filter((m) => !m.is_canonical).map((m) => m.row_id),
      );
      return apiFetch<{ id: string; version_number: number }>(
        `/versions/${activeVersionId}/curation`,
        {
          method: 'POST',
          body: JSON.stringify({
            actions: nonCanonIds.map((row_id) => ({ row_id, action })),
          }),
        },
        accessToken,
      );
    },
    onSuccess: (res) => {
      setErrBanner(null);
      setBanner(t('dedup.actionSuccess', { version: res.version_number }));
      setSearchParams({ versionId: res.id });
      void queryClient.invalidateQueries({ queryKey: ['datasets', activeWorkspaceId] });
      void queryClient.invalidateQueries({ queryKey: ['datasets-for-dedup', activeWorkspaceId] });
    },
    onError: (err) => {
      setBanner(null);
      setErrBanner(err instanceof ApiError ? err.message : t('dedup.actionError'));
    },
  });

  const groups = previewQuery.data?.groups ?? [];
  const duplicateCount = previewQuery.data?.duplicate_rows_count ?? 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-text-primary">{t('dedup.title')}</h1>
        <p className="mt-1 text-sm text-text-secondary">{t('dedup.subtitle')}</p>
      </div>

      {!versionId && (datasetsQuery.data?.length ?? 0) > 0 && (
        <Card className="flex flex-wrap items-center gap-3 p-4">
          <label htmlFor="dedup-dataset" className="text-xs font-medium text-text-secondary">
            {t('dedup.selectDatasetLabel')}
          </label>
          <select
            id="dedup-dataset"
            value={activeVersionId}
            onChange={(e) => setSearchParams({ versionId: e.target.value })}
            className="rounded-control border border-border bg-surface px-2.5 py-1.5 text-sm text-text-primary"
          >
            {datasetsQuery.data
              ?.filter((d) => d.latest_version)
              .map((d) => (
                <option key={d.id} value={d.latest_version!.id}>
                  {d.name} (v{d.latest_version!.version_number})
                </option>
              ))}
          </select>
        </Card>
      )}

      {!activeVersionId ? (
        <EmptyState
          icon={<GitCompare className="h-10 w-10" />}
          title={t('dedup.emptyVersionTitle')}
          description={t('dedup.emptyVersionDesc')}
        />
      ) : (
        <>
          <Card className="space-y-4 p-4">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="flex-1 min-w-[220px]">
                <label
                  htmlFor="dedup-threshold"
                  className="mb-1 block text-xs font-medium text-text-primary"
                >
                  {t('dedup.thresholdLabel', { pct: Math.round(threshold * 100) })}
                </label>
                <input
                  id="dedup-threshold"
                  type="range"
                  min={0.5}
                  max={0.99}
                  step={0.01}
                  value={threshold}
                  onChange={(e) => setThreshold(Number(e.target.value))}
                  className="w-full accent-brand-600"
                />
              </div>

              {duplicateCount > 0 && (
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    disabled={curateMutation.isPending}
                    onClick={() => curateMutation.mutate('reject')}
                  >
                    {t('dedup.rejectDuplicatesBtn')}
                  </Button>
                  <Button
                    variant="destructive"
                    disabled={curateMutation.isPending}
                    onClick={() => curateMutation.mutate('drop_row')}
                  >
                    {t('dedup.dropDuplicatesBtn')}
                  </Button>
                </div>
              )}
            </div>

            {previewQuery.data && (
              <p className="text-xs text-text-muted">
                {t('dedup.summaryStats', {
                  groups: formatNumber(previewQuery.data.duplicate_groups_count, locale),
                  dups: formatNumber(previewQuery.data.duplicate_rows_count, locale),
                  backend: previewQuery.data.embedding_backend,
                })}
              </p>
            )}
          </Card>

          {banner && (
            <div className="rounded-control border border-border bg-subtle p-3 text-xs text-success-600">
              {banner}
            </div>
          )}
          {errBanner && (
            <div
              role="alert"
              className="rounded-control border border-border bg-subtle p-3 text-xs text-danger-600"
            >
              {errBanner}
            </div>
          )}

          {previewQuery.isLoading && <Skeleton className="h-48 w-full" />}
          {previewQuery.error && (
            <ErrorState
              message={
                previewQuery.error instanceof ApiError
                  ? previewQuery.error.message
                  : t('dedup.loadError')
              }
              onRetry={() => previewQuery.refetch()}
            />
          )}

          {previewQuery.data && groups.length === 0 && (
            <Card className="p-6 text-center text-sm text-text-muted">
              {t('dedup.noDuplicates', { pct: Math.round(threshold * 100) })}
            </Card>
          )}

          <div className="space-y-4">
            {groups.map((grp) => (
              <Card key={grp.group_id} className="space-y-3 p-4">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs font-semibold text-text-secondary">
                    {grp.group_id}
                  </span>
                  <Badge tone="info">
                    {t('dedup.similarityBadge', { pct: Math.round(grp.avg_similarity * 100) })}
                  </Badge>
                </div>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  {grp.members.map((m) => (
                    <div
                      key={m.row_id}
                      className={`rounded-control border p-3 text-xs ${
                        m.is_canonical
                          ? 'border-brand-600 bg-brand-50/40'
                          : 'border-border bg-canvas'
                      }`}
                    >
                      <div className="mb-1.5 flex items-center justify-between gap-2">
                        <span className="font-mono font-semibold text-text-primary">
                          {m.row_id}
                        </span>
                        {m.is_canonical ? (
                          <Badge tone="success">{t('dedup.canonicalBadge')}</Badge>
                        ) : (
                          <Badge tone="warning">
                            {t('dedup.similarityBadge', { pct: Math.round(m.similarity * 100) })}
                          </Badge>
                        )}
                      </div>
                      <p className="font-mono text-text-secondary">{m.text_preview}</p>
                    </div>
                  ))}
                </div>
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
