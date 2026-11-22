import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Download, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';
import { formatDate, useTranslation } from '@/lib/i18n';

interface AuditEntry {
  id: string;
  created_at: string;
  user_id: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  ip_address?: string | null;
}

export function AuditLogsPage() {
  const { accessToken, activeWorkspaceId } = useAuthStore();
  const { t, locale } = useTranslation();
  const [actionFilter, setActionFilter] = useState('');
  const [exportError, setExportError] = useState<string | null>(null);

  const trimmedAction = actionFilter.trim();
  const queryPath = trimmedAction
    ? `/workspaces/${activeWorkspaceId}/audit-logs?action=${encodeURIComponent(trimmedAction)}`
    : `/workspaces/${activeWorkspaceId}/audit-logs`;

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['audit-logs', activeWorkspaceId, trimmedAction],
    enabled: Boolean(activeWorkspaceId && accessToken),
    queryFn: () => apiFetch<AuditEntry[]>(queryPath, { method: 'GET' }, accessToken),
  });

  async function handleExport(format: 'csv' | 'json') {
    if (!activeWorkspaceId) return;
    setExportError(null);
    try {
      const base = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1';
      const qs = trimmedAction
        ? `format=${format}&action=${encodeURIComponent(trimmedAction)}`
        : `format=${format}`;
      const res = await fetch(`${base}/workspaces/${activeWorkspaceId}/audit-logs/export?${qs}`, {
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
      anchor.download = `audit_logs.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch {
      setExportError(t('audit.exportError'));
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold text-text-primary">{t('audit.title')}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            aria-label={t('audit.filterActionPlaceholder')}
            placeholder={t('audit.filterActionPlaceholder')}
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            className="w-60 text-xs"
          />
          <Button
            variant="secondary"
            className="px-3 py-1.5 text-xs"
            onClick={() => void handleExport('csv')}
          >
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{t('audit.exportCsv')}</span>
          </Button>
          <Button
            variant="secondary"
            className="px-3 py-1.5 text-xs"
            onClick={() => void handleExport('json')}
          >
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{t('audit.exportJson')}</span>
          </Button>
        </div>
      </div>

      {exportError && (
        <div
          role="alert"
          className="rounded-control border border-border bg-subtle p-3 text-xs text-danger-600"
        >
          {exportError}
        </div>
      )}

      {isLoading && <Skeleton className="h-48 w-full" />}
      {error && (
        <ErrorState
          message={error instanceof ApiError ? error.message : t('audit.loadError')}
          onRetry={() => refetch()}
        />
      )}
      {data && data.length === 0 && (
        <EmptyState
          icon={<ShieldCheck className="h-10 w-10" />}
          title={t('audit.emptyTitle')}
          description={t('audit.emptyDescription')}
        />
      )}
      {data && data.length > 0 && (
        <Card className="overflow-x-auto p-0">
          <table className="w-full border-collapse text-left text-xs">
            <thead className="border-b border-border bg-subtle text-text-secondary">
              <tr>
                <th className="p-3">{t('audit.colTime')}</th>
                <th className="p-3">{t('audit.colAction')}</th>
                <th className="p-3">{t('audit.colTarget')}</th>
                <th className="p-3">{t('audit.colTargetId')}</th>
                <th className="p-3">{t('audit.colIp')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.map((row) => (
                <tr key={row.id}>
                  <td className="p-3 font-mono text-text-muted">
                    {formatDate(row.created_at, locale)}
                  </td>
                  <td className="p-3 font-medium text-text-primary">{row.action}</td>
                  <td className="p-3 text-text-secondary">{row.target_type}</td>
                  <td className="p-3 font-mono text-text-muted">{row.target_id ?? '—'}</td>
                  <td className="p-3 font-mono text-text-muted">{row.ip_address ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
