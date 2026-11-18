import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Database, Play, Upload } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';
import { formatNumber, useTranslation } from '@/lib/i18n';

interface DatasetVersionSummary {
  id: string;
  version_number: number;
  source_format: string;
  row_count: number;
  invalid_row_count: number;
  status: string;
}

interface DatasetItem {
  id: string;
  name: string;
  description: string;
  dataset_type: string;
  latest_version: DatasetVersionSummary | null;
}

export function DatasetsPage() {
  const { accessToken, activeWorkspaceId } = useAuthStore();
  const { locale } = useTranslation();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [name, setName] = useState('');
  const [datasetType, setDatasetType] = useState<'sft' | 'preference' | 'rag' | 'raw'>('sft');
  const [uploadDatasetId, setUploadDatasetId] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['datasets', activeWorkspaceId],
    enabled: Boolean(activeWorkspaceId && accessToken),
    queryFn: () =>
      apiFetch<DatasetItem[]>(
        `/workspaces/${activeWorkspaceId}/datasets`,
        { method: 'GET' },
        accessToken,
      ),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      apiFetch<DatasetItem>(
        `/workspaces/${activeWorkspaceId}/datasets`,
        {
          method: 'POST',
          body: JSON.stringify({ name, description: '', dataset_type: datasetType }),
        },
        accessToken,
      ),
    onSuccess: (created) => {
      setName('');
      setUploadDatasetId(created.id);
      queryClient.invalidateQueries({ queryKey: ['datasets', activeWorkspaceId] });
    },
  });

  const runMutation = useMutation({
    mutationFn: (versionId: string) =>
      apiFetch<{ id: string }>(
        `/versions/${versionId}/runs`,
        { method: 'POST', body: JSON.stringify({ seed: 42 }) },
        accessToken,
      ),
    onSuccess: (run) => {
      navigate(`/runs?runId=${run.id}`);
    },
  });

  async function handleFileUpload(datasetId: string, file: File) {
    setActionMsg(null);
    const ext = file.name.split('.').pop()?.toLowerCase();
    const fmt = ext === 'csv' || ext === 'parquet' || ext === 'txt' ? ext : 'jsonl';
    const form = new FormData();
    form.append('file', file);
    form.append('source_format', fmt);

    const baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1';
    const res = await fetch(`${baseUrl}/datasets/${datasetId}/versions/upload-direct`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${accessToken}` },
      body: form,
    });
    if (!res.ok) {
      setActionMsg('Gagal mengunggah berkas. Pastikan format valid.');
      return;
    }
    setUploadDatasetId(null);
    setActionMsg('Dataset berhasil diunggah dan divalidasi.');
    queryClient.invalidateQueries({ queryKey: ['datasets', activeWorkspaceId] });
  }

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    createMutation.mutate();
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">Dataset</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Unggah JSONL, CSV, Parquet, atau teks mentah lalu jalankan penilaian kualitas.
          </p>
        </div>
      </div>

      <Card>
        <form onSubmit={handleCreate} className="flex flex-wrap items-end gap-3">
          <div className="min-w-[240px] flex-1">
            <label htmlFor="ds-name" className="mb-1 block text-xs font-medium text-text-muted">
              Nama Dataset Baru
            </label>
            <Input
              id="ds-name"
              placeholder="Contoh: SFT Customer Support ID"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div>
            <label htmlFor="ds-type" className="mb-1 block text-xs font-medium text-text-muted">
              Tipe
            </label>
            <select
              id="ds-type"
              value={datasetType}
              onChange={(e) => setDatasetType(e.target.value as typeof datasetType)}
              className="rounded-control border border-border bg-surface px-3 py-2 text-sm text-text-primary"
            >
              <option value="sft">SFT (Prompt/Response)</option>
              <option value="preference">Preference (DPO/RLHF)</option>
              <option value="rag">RAG Chunks</option>
              <option value="raw">Raw Text</option>
            </select>
          </div>
          <Button type="submit" disabled={createMutation.isPending}>
            Buat Dataset
          </Button>
        </form>
        {actionMsg && <p className="mt-3 text-xs text-info-600">{actionMsg}</p>}
      </Card>

      {isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      )}

      {error && (
        <ErrorState
          message={error instanceof ApiError ? error.message : 'Gagal memuat daftar dataset.'}
          onRetry={() => refetch()}
        />
      )}

      {data && data.length === 0 && (
        <EmptyState
          icon={<Database className="h-10 w-10" />}
          title="Belum ada dataset"
          description="Buat dataset baru di atas, lalu unggah berkas JSONL/CSV/Parquet Anda."
        />
      )}

      {data && data.length > 0 && (
        <div className="space-y-3">
          {data.map((ds) => (
            <Card key={ds.id} className="flex flex-wrap items-center justify-between gap-4 p-4">
              <div>
                <div className="flex items-center gap-2">
                  <h2 className="text-base font-semibold text-text-primary">{ds.name}</h2>
                  <Badge>{ds.dataset_type.toUpperCase()}</Badge>
                  {ds.latest_version && (
                    <Badge tone="info">v{ds.latest_version.version_number}</Badge>
                  )}
                </div>
                {ds.latest_version ? (
                  <p className="mt-1 text-xs text-text-secondary tabular-nums">
                    {formatNumber(ds.latest_version.row_count, locale)} baris ·{' '}
                    {formatNumber(ds.latest_version.invalid_row_count, locale)} tidak valid ·
                    Status: {ds.latest_version.status}
                  </p>
                ) : (
                  <p className="mt-1 text-xs text-text-muted">Belum ada berkas yang diunggah.</p>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-control border border-border bg-surface px-3 py-1.5 text-xs font-medium text-text-primary hover:bg-subtle">
                  <Upload className="h-3.5 w-3.5" />
                  <span>Unggah Versi</span>
                  <input
                    type="file"
                    accept=".jsonl,.csv,.parquet,.txt"
                    className="sr-only"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) void handleFileUpload(ds.id, file);
                    }}
                  />
                </label>

                {ds.latest_version && (
                  <>
                    <Button
                      variant="secondary"
                      onClick={() => navigate(`/explorer?versionId=${ds.latest_version!.id}`)}
                      className="px-3 py-1.5 text-xs"
                    >
                      Buka Explorer
                    </Button>
                    <Button
                      onClick={() => runMutation.mutate(ds.latest_version!.id)}
                      disabled={runMutation.isPending}
                      className="px-3 py-1.5 text-xs"
                    >
                      <Play className="h-3.5 w-3.5" />
                      <span>Jalankan Penilaian</span>
                    </Button>
                  </>
                )}
              </div>

              {uploadDatasetId === ds.id && (
                <p className="w-full text-xs text-text-muted">
                  Dataset dibuat — klik &ldquo;Unggah Versi&rdquo; untuk mengunggah berkas Anda.
                </p>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
