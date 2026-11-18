import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Download, Eye, ShieldAlert, TableProperties, X } from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';

interface RowItem {
  row_id: string;
  line_number: number;
  prompt: string;
  response: string;
  is_valid: boolean;
  validation_error: string;
  curation_status: string;
  has_pii: boolean;
}

interface PaginatedRows {
  version_id: string;
  total: number;
  pii_revealed: boolean;
  items: RowItem[];
}

export function ExplorerPage() {
  const { accessToken } = useAuthStore();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const versionId = searchParams.get('versionId');

  const [search, setSearch] = useState('');
  const [revealPii, setRevealPii] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [feedback, setFeedback] = useState<string | null>(null);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['explorer-rows', versionId, search, revealPii],
    enabled: Boolean(versionId && accessToken),
    queryFn: () => {
      const params = new URLSearchParams({ limit: '50', offset: '0' });
      if (search) params.set('search', search);
      if (revealPii) params.set('reveal_pii', 'true');
      return apiFetch<PaginatedRows>(
        `/versions/${versionId}/rows?${params.toString()}`,
        { method: 'GET' },
        accessToken,
      );
    },
  });

  const curateMutation = useMutation({
    mutationFn: (action: 'approve' | 'reject' | 'mask_pii') =>
      apiFetch<{ id: string; version_number: number }>(
        `/versions/${versionId}/curation`,
        {
          method: 'POST',
          body: JSON.stringify({
            actions: selectedIds.map((row_id) => ({ row_id, action })),
          }),
        },
        accessToken,
      ),
    onSuccess: (newVer) => {
      setSelectedIds([]);
      setFeedback(`Versi baru v${newVer.version_number} berhasil dibuat.`);
      navigate(`/explorer?versionId=${newVer.id}`);
      queryClient.invalidateQueries({ queryKey: ['explorer-rows'] });
    },
    onError: (err) => {
      setFeedback(err instanceof ApiError ? err.message : 'Gagal menyimpan kurasi.');
    },
  });

  const exportMutation = useMutation({
    mutationFn: (format: 'jsonl' | 'csv' | 'parquet') =>
      apiFetch<{ id: string }>(
        `/versions/${versionId}/exports`,
        { method: 'POST', body: JSON.stringify({ export_format: format }) },
        accessToken,
      ),
    onSuccess: (exp) => {
      setFeedback(`Ekspor berhasil dibuat (ID: ${exp.id}).`);
    },
    onError: (err) => {
      setFeedback(err instanceof ApiError ? err.message : 'Ekspor ditolak oleh Quality Gate.');
    },
  });

  if (!versionId) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold text-text-primary">Explorer & Kurasi</h1>
        <EmptyState
          icon={<TableProperties className="h-10 w-10" />}
          title="Belum ada versi dataset yang dipilih"
          description="Pilih dataset di halaman Dataset lalu klik 'Buka Explorer'."
        />
      </div>
    );
  }

  function toggleRow(id: string) {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">Explorer & Kurasi</h1>
          <p className="font-mono text-xs text-text-muted">Version ID: {versionId}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            onClick={() => setRevealPii((v) => !v)}
            className="px-3 py-1.5 text-xs"
          >
            <Eye className="h-3.5 w-3.5" />
            <span>{revealPii ? 'Samarkan PII' : 'Buka PII (Izin Khusus)'}</span>
          </Button>
          <Button
            variant="secondary"
            onClick={() => exportMutation.mutate('jsonl')}
            disabled={exportMutation.isPending}
            className="px-3 py-1.5 text-xs"
          >
            <Download className="h-3.5 w-3.5" />
            <span>Ekspor JSONL</span>
          </Button>
        </div>
      </div>

      <Card className="flex flex-wrap items-center justify-between gap-3 p-3">
        <Input
          placeholder="Cari teks atau row_id..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />

        {selectedIds.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-text-secondary">{selectedIds.length} baris dipilih</span>
            <Button
              variant="secondary"
              onClick={() => curateMutation.mutate('approve')}
              className="px-2.5 py-1 text-xs"
            >
              <Check className="h-3.5 w-3.5" /> Setujui
            </Button>
            <Button
              variant="secondary"
              onClick={() => curateMutation.mutate('mask_pii')}
              className="px-2.5 py-1 text-xs"
            >
              <ShieldAlert className="h-3.5 w-3.5" /> Masking PII
            </Button>
            <Button
              variant="destructive"
              onClick={() => curateMutation.mutate('reject')}
              className="px-2.5 py-1 text-xs"
            >
              <X className="h-3.5 w-3.5" /> Tolak
            </Button>
          </div>
        )}
      </Card>

      {feedback && (
        <div className="rounded-control border border-border bg-surface p-3 text-xs text-text-primary">
          {feedback}
        </div>
      )}

      {isLoading && <Skeleton className="h-64 w-full" />}
      {error && (
        <ErrorState
          message={error instanceof ApiError ? error.message : 'Gagal memuat baris dataset.'}
          onRetry={() => refetch()}
        />
      )}

      {data && (
        <Card className="overflow-x-auto p-0">
          <table className="w-full border-collapse text-left text-xs">
            <thead className="sticky top-0 border-b border-border bg-subtle text-text-secondary">
              <tr>
                <th className="p-3">Pilih</th>
                <th className="p-3">Row ID</th>
                <th className="p-3">Prompt</th>
                <th className="p-3">Response</th>
                <th className="p-3">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.items.map((row) => (
                <tr key={row.row_id} className="hover:bg-subtle">
                  <td className="p-3">
                    <input
                      type="checkbox"
                      aria-label={`Pilih ${row.row_id}`}
                      checked={selectedIds.includes(row.row_id)}
                      onChange={() => toggleRow(row.row_id)}
                    />
                  </td>
                  <td className="p-3 font-mono text-text-muted">{row.row_id}</td>
                  <td className="max-w-md truncate p-3 font-mono text-text-primary">
                    {row.prompt || '—'}
                  </td>
                  <td className="max-w-md truncate p-3 font-mono text-text-secondary">
                    {row.response || '—'}
                  </td>
                  <td className="p-3">
                    <div className="flex items-center gap-1.5">
                      <Badge
                        tone={
                          row.curation_status === 'approved'
                            ? 'success'
                            : row.curation_status === 'rejected'
                              ? 'danger'
                              : 'neutral'
                        }
                      >
                        {row.curation_status}
                      </Badge>
                      {row.has_pii && <Badge tone="danger">PII</Badge>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
