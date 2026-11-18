import { useQuery } from '@tanstack/react-query';
import { Activity, Download } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { ScoreCard } from '@/components/shared/ScoreCard';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';

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

export function RunsPage() {
  const { accessToken } = useAuthStore();
  const [searchParams] = useSearchParams();
  const runId = searchParams.get('runId');

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['run-summary', runId],
    enabled: Boolean(runId && accessToken),
    queryFn: () =>
      apiFetch<RunSummary>(`/runs/${runId}/summary`, { method: 'GET' }, accessToken),
  });

  if (!runId) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold text-text-primary">Laporan Run Kualitas</h1>
        <EmptyState
          icon={<Activity className="h-10 w-10" />}
          title="Pilih atau jalankan penilaian dataset"
          description="Buka halaman Dataset dan klik 'Jalankan Penilaian' untuk melihat DQI dan rincian temuan."
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">Laporan Run Kualitas</h1>
          <p className="mt-1 font-mono text-xs text-text-muted">Run ID: {runId}</p>
        </div>
        {data && (
          <div className="flex items-center gap-2">
            <Badge tone="info">{data.status}</Badge>
            <Button
              variant="secondary"
              className="px-3 py-1.5 text-xs"
              onClick={() => {
                const base = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1';
                window.open(`${base}/runs/${data.run_id}/report.html`, '_blank');
              }}
            >
              <Download className="h-3.5 w-3.5" />
              <span>Unduh Laporan HTML</span>
            </Button>
          </div>
        )}
      </div>

      {isLoading && <Skeleton className="h-48 w-full" />}
      {error && (
        <ErrorState
          message={error instanceof ApiError ? error.message : 'Gagal memuat laporan run.'}
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
          />

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {data.module_results.map((mr) => (
              <Card key={mr.module} className="space-y-2 p-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-text-primary">{mr.module}</h2>
                  <Badge>{mr.findings_count} temuan</Badge>
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
