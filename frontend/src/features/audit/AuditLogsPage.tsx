import { useQuery } from '@tanstack/react-query';
import { Card } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';

interface AuditEntry {
  id: string;
  created_at: string;
  user_id: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
}

export function AuditLogsPage() {
  const { accessToken, activeWorkspaceId } = useAuthStore();

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['audit-logs', activeWorkspaceId],
    enabled: Boolean(activeWorkspaceId && accessToken),
    queryFn: () =>
      apiFetch<AuditEntry[]>(
        `/workspaces/${activeWorkspaceId}/audit-logs`,
        { method: 'GET' },
        accessToken,
      ),
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-text-primary">Audit Log</h1>
      {isLoading && <Skeleton className="h-48 w-full" />}
      {error && (
        <ErrorState
          message={error instanceof ApiError ? error.message : 'Gagal memuat audit log.'}
          onRetry={() => refetch()}
        />
      )}
      {data && (
        <Card className="overflow-x-auto p-0">
          <table className="w-full border-collapse text-left text-xs">
            <thead className="border-b border-border bg-subtle text-text-secondary">
              <tr>
                <th className="p-3">Waktu</th>
                <th className="p-3">Aksi</th>
                <th className="p-3">Objek</th>
                <th className="p-3">ID Target</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.map((row) => (
                <tr key={row.id}>
                  <td className="p-3 font-mono text-text-muted">{row.created_at}</td>
                  <td className="p-3 font-medium text-text-primary">{row.action}</td>
                  <td className="p-3 text-text-secondary">{row.target_type}</td>
                  <td className="p-3 font-mono text-text-muted">{row.target_id ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
