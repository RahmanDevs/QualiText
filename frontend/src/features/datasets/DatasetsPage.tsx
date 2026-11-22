import { useEffect, useState, type DragEvent, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Columns3,
  Database,
  Download,
  Play,
  Plug,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
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

const CHUNK_SIZE_BYTES = 5 * 1024 * 1024;

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
  retention_days?: number | null;
  latest_version: DatasetVersionSummary | null;
}

interface ColumnMappingResponse {
  version_id: string;
  mapping: Record<string, string>;
  detected_columns: string[];
}

interface ValidationErrorsResponse {
  version_id: string;
  invalid_row_count: number;
  errors: Array<{
    line_number: number;
    error_type: string;
    message: string;
  }>;
}

function detectFormat(fileName: string): 'jsonl' | 'csv' | 'parquet' | 'txt' | 'pdf' | 'md' {
  const ext = fileName.split('.').pop()?.toLowerCase();
  if (ext === 'csv' || ext === 'parquet' || ext === 'txt' || ext === 'pdf' || ext === 'md') {
    return ext;
  }
  return 'jsonl';
}

function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function DatasetsPage() {
  const { accessToken, activeWorkspaceId } = useAuthStore();
  const { t, locale } = useTranslation();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [name, setName] = useState('');
  const [datasetType, setDatasetType] = useState<'sft' | 'preference' | 'rag' | 'raw'>('sft');
  const [retentionDays, setRetentionDays] = useState('');
  const [uploadDatasetId, setUploadDatasetId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<{ datasetId: string; pct: number } | null>(
    null,
  );
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const [mappingVersionId, setMappingVersionId] = useState<string | null>(null);
  const [draftMapping, setDraftMapping] = useState<Record<string, string>>({});
  const [mappingNotice, setMappingNotice] = useState<string | null>(null);

  const [errorsVersionId, setErrorsVersionId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const [importDatasetId, setImportDatasetId] = useState<string | null>(null);
  const [connectorType, setConnectorType] = useState<'s3' | 'database' | 'sharepoint'>('s3');
  const [connectorUri, setConnectorUri] = useState('');
  const [connectorFormat, setConnectorFormat] = useState<
    'jsonl' | 'csv' | 'parquet' | 'txt' | 'pdf' | 'md'
  >('jsonl');
  const [connectorTable, setConnectorTable] = useState('');
  const [connectorQuery, setConnectorQuery] = useState('');
  const [connectorCredRef, setConnectorCredRef] = useState('');
  const [connectorError, setConnectorError] = useState<string | null>(null);

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

  const mappingQuery = useQuery({
    queryKey: ['version-mapping', mappingVersionId],
    enabled: Boolean(mappingVersionId && accessToken),
    queryFn: () =>
      apiFetch<ColumnMappingResponse>(
        `/versions/${mappingVersionId}/mapping`,
        { method: 'GET' },
        accessToken,
      ),
  });

  useEffect(() => {
    if (mappingQuery.data) {
      setDraftMapping(mappingQuery.data.mapping ?? {});
    }
  }, [mappingQuery.data]);

  const errorsQuery = useQuery({
    queryKey: ['version-errors', errorsVersionId],
    enabled: Boolean(errorsVersionId && accessToken),
    queryFn: () =>
      apiFetch<ValidationErrorsResponse>(
        `/versions/${errorsVersionId}/validation-errors`,
        { method: 'GET' },
        accessToken,
      ),
  });

  const createMutation = useMutation({
    mutationFn: () => {
      const parsedDays = retentionDays.trim() ? Number(retentionDays) : undefined;
      return apiFetch<DatasetItem>(
        `/workspaces/${activeWorkspaceId}/datasets`,
        {
          method: 'POST',
          body: JSON.stringify({
            name,
            description: '',
            dataset_type: datasetType,
            ...(parsedDays !== undefined && Number.isFinite(parsedDays) && parsedDays >= 1
              ? { retention_days: parsedDays }
              : {}),
          }),
        },
        accessToken,
      );
    },
    onSuccess: (created) => {
      setName('');
      setRetentionDays('');
      setActionMsg(null);
      setUploadDatasetId(created.id);
      queryClient.invalidateQueries({ queryKey: ['datasets', activeWorkspaceId] });
    },
    onError: (err) => {
      setActionMsg(err instanceof ApiError ? err.message : t('datasets.loadError'));
    },
  });

  const connectorImportMutation = useMutation({
    mutationFn: () => {
      const options: Record<string, unknown> = {};
      if (connectorType === 'database') {
        if (connectorTable.trim()) options.table = connectorTable.trim();
        if (connectorQuery.trim()) options.query = connectorQuery.trim();
      }
      return apiFetch<DatasetVersionSummary>(
        `/datasets/${importDatasetId}/versions/import-connector`,
        {
          method: 'POST',
          body: JSON.stringify({
            connector_type: connectorType,
            uri: connectorUri.trim(),
            credentials_ref: connectorCredRef.trim(),
            source_format: connectorType === 'database' ? 'jsonl' : connectorFormat,
            options,
          }),
        },
        accessToken,
      );
    },
    onSuccess: () => {
      setImportDatasetId(null);
      setConnectorUri('');
      setConnectorTable('');
      setConnectorQuery('');
      setConnectorCredRef('');
      setConnectorError(null);
      setActionMsg(t('datasets.connectorSuccess'));
      void queryClient.invalidateQueries({ queryKey: ['datasets', activeWorkspaceId] });
    },
    onError: (err) => {
      setConnectorError(err instanceof ApiError ? err.message : t('datasets.connectorError'));
    },
  });

  const deleteDatasetMutation = useMutation({
    mutationFn: (datasetId: string) =>
      apiFetch<void>(`/datasets/${datasetId}`, { method: 'DELETE' }, accessToken),
    onSuccess: () => {
      setConfirmDeleteId(null);
      setActionMsg(t('datasets.deleteSuccess'));
      void queryClient.invalidateQueries({ queryKey: ['datasets', activeWorkspaceId] });
    },
    onError: (err) => {
      setConfirmDeleteId(null);
      setActionMsg(err instanceof ApiError ? err.message : t('datasets.deleteError'));
    },
  });

  const saveMappingMutation = useMutation({
    mutationFn: () => {
      const cleaned: Record<string, string> = {};
      for (const [col, role] of Object.entries(draftMapping)) {
        if (role) cleaned[col] = role;
      }
      return apiFetch<DatasetVersionSummary>(
        `/versions/${mappingVersionId}/mapping`,
        { method: 'PUT', body: JSON.stringify({ mapping: cleaned }) },
        accessToken,
      );
    },
    onSuccess: () => {
      setMappingNotice(t('datasets.mappingSaved'));
      queryClient.invalidateQueries({ queryKey: ['datasets', activeWorkspaceId] });
      queryClient.invalidateQueries({ queryKey: ['version-mapping', mappingVersionId] });
    },
    onError: (err) => {
      setActionMsg(err instanceof ApiError ? err.message : t('datasets.loadError'));
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
    onError: (err) => {
      setActionMsg(err instanceof ApiError ? err.message : t('runs.loadError'));
    },
  });

  async function handleFileUpload(datasetId: string, file: File) {
    setActionMsg(null);
    const fmt = detectFormat(file.name);
    const baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1';

    try {
      if (file.size <= CHUNK_SIZE_BYTES) {
        setUploadProgress({ datasetId, pct: 50 });
        const form = new FormData();
        form.append('file', file);
        form.append('source_format', fmt);
        const res = await fetch(`${baseUrl}/datasets/${datasetId}/versions/upload-direct`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${accessToken}` },
          body: form,
        });
        if (!res.ok) {
          const errBody = (await res.json().catch(() => null)) as {
            error?: { message?: string };
          } | null;
          throw new Error(errBody?.error?.message || t('datasets.uploadError'));
        }
      } else {
        const totalChunks = Math.ceil(file.size / CHUNK_SIZE_BYTES);
        const init = await apiFetch<{ version_id: string }>(
          `/datasets/${datasetId}/versions/uploads`,
          {
            method: 'POST',
            body: JSON.stringify({
              filename: file.name,
              source_format: fmt,
              total_chunks: totalChunks,
            }),
          },
          accessToken,
        );

        for (let idx = 0; idx < totalChunks; idx += 1) {
          const slice = file.slice(idx * CHUNK_SIZE_BYTES, (idx + 1) * CHUNK_SIZE_BYTES);
          const putRes = await fetch(
            `${baseUrl}/datasets/${datasetId}/versions/${init.version_id}/chunks/${idx}`,
            {
              method: 'PUT',
              headers: {
                Authorization: `Bearer ${accessToken}`,
                'Content-Type': 'application/octet-stream',
              },
              body: slice,
            },
          );
          if (!putRes.ok) {
            const errBody = (await putRes.json().catch(() => null)) as {
              error?: { message?: string };
            } | null;
            throw new Error(errBody?.error?.message || t('datasets.uploadError'));
          }
          setUploadProgress({
            datasetId,
            pct: Math.round(((idx + 1) / totalChunks) * 100),
          });
        }

        await apiFetch<DatasetVersionSummary>(
          `/datasets/${datasetId}/versions/${init.version_id}/complete?total_chunks=${totalChunks}`,
          { method: 'POST' },
          accessToken,
        );
      }

      setUploadDatasetId(null);
      setActionMsg(t('datasets.uploadSuccess'));
      queryClient.invalidateQueries({ queryKey: ['datasets', activeWorkspaceId] });
    } catch (err) {
      setActionMsg(err instanceof Error && err.message ? err.message : t('datasets.uploadError'));
    } finally {
      setUploadProgress(null);
    }
  }

  function handleDrop(e: DragEvent<HTMLDivElement>, datasetId: string) {
    e.preventDefault();
    setDragOverId(null);
    const file = e.dataTransfer.files?.[0];
    if (file) {
      void handleFileUpload(datasetId, file);
    }
  }

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    createMutation.mutate();
  }

  function downloadErrors(format: 'json' | 'csv') {
    if (!errorsQuery.data) return;
    if (format === 'json') {
      const blob = new Blob([JSON.stringify(errorsQuery.data, null, 2)], {
        type: 'application/json',
      });
      triggerBlobDownload(blob, `validation_errors_${errorsQuery.data.version_id}.json`);
      return;
    }
    const header = 'line_number,error_type,message\n';
    const rows = errorsQuery.data.errors
      .map((err) => {
        const escapedMsg = `"${err.message.replaceAll('"', '""')}"`;
        return `${err.line_number},${err.error_type},${escapedMsg}`;
      })
      .join('\n');
    const blob = new Blob([header + rows], { type: 'text/csv;charset=utf-8' });
    triggerBlobDownload(blob, `validation_errors_${errorsQuery.data.version_id}.csv`);
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">{t('datasets.title')}</h1>
          <p className="mt-1 text-sm text-text-secondary">{t('datasets.subtitle')}</p>
        </div>
      </div>

      <Card>
        <form onSubmit={handleCreate} className="flex flex-wrap items-end gap-3">
          <div className="min-w-[240px] flex-1">
            <label htmlFor="ds-name" className="mb-1 block text-xs font-medium text-text-muted">
              {t('datasets.newNameLabel')}
            </label>
            <Input
              id="ds-name"
              placeholder={t('datasets.newNamePlaceholder')}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div>
            <label htmlFor="ds-type" className="mb-1 block text-xs font-medium text-text-muted">
              {t('datasets.typeLabel')}
            </label>
            <select
              id="ds-type"
              value={datasetType}
              onChange={(e) => setDatasetType(e.target.value as typeof datasetType)}
              className="rounded-control border border-border bg-surface px-3 py-2 text-sm text-text-primary"
            >
              <option value="sft">{t('datasets.typeSft')}</option>
              <option value="preference">{t('datasets.typePreference')}</option>
              <option value="rag">{t('datasets.typeRag')}</option>
              <option value="raw">{t('datasets.typeRaw')}</option>
            </select>
          </div>
          <div className="w-40">
            <label
              htmlFor="ds-retention"
              className="mb-1 block text-xs font-medium text-text-muted"
            >
              {t('datasets.retentionDaysLabel')}
            </label>
            <Input
              id="ds-retention"
              type="number"
              min={1}
              max={3650}
              placeholder={t('datasets.retentionDaysPlaceholder')}
              value={retentionDays}
              onChange={(e) => setRetentionDays(e.target.value)}
            />
          </div>
          <Button type="submit" disabled={createMutation.isPending}>
            {t('datasets.createButton')}
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
          message={error instanceof ApiError ? error.message : t('datasets.loadError')}
          onRetry={() => refetch()}
        />
      )}

      {data && data.length === 0 && (
        <EmptyState
          icon={<Database className="h-10 w-10" />}
          title={t('datasets.emptyTitle')}
          description={t('datasets.emptyDescription')}
        />
      )}

      {data && data.length > 0 && (
        <div className="space-y-3">
          {data.map((ds) => {
            const isUploadingThis = uploadProgress?.datasetId === ds.id;
            const isDragging = dragOverId === ds.id;
            return (
              <Card
                key={ds.id}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOverId(ds.id);
                }}
                onDragLeave={() => setDragOverId(null)}
                onDrop={(e) => handleDrop(e, ds.id)}
                className={`space-y-3 p-4 transition-colors ${
                  isDragging ? 'border-brand-600 bg-brand-50' : ''
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-base font-semibold text-text-primary">{ds.name}</h2>
                      <Badge>{ds.dataset_type.toUpperCase()}</Badge>
                      {ds.latest_version && (
                        <Badge tone="info">v{ds.latest_version.version_number}</Badge>
                      )}
                      {ds.retention_days && (
                        <Badge tone="neutral">
                          {t('datasets.retentionBadge', { days: ds.retention_days })}
                        </Badge>
                      )}
                    </div>
                    {ds.latest_version ? (
                      <p className="mt-1 text-xs text-text-secondary tabular-nums">
                        {t('datasets.rowStats', {
                          rows: formatNumber(ds.latest_version.row_count, locale),
                          invalid: formatNumber(ds.latest_version.invalid_row_count, locale),
                          status: ds.latest_version.status,
                        })}
                      </p>
                    ) : (
                      <p className="mt-1 text-xs text-text-muted">{t('datasets.noUploadYet')}</p>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-control border border-border bg-surface px-3 py-1.5 text-xs font-medium text-text-primary hover:bg-subtle">
                      <Upload className="h-3.5 w-3.5" aria-hidden="true" />
                      <span>
                        {isUploadingThis
                          ? t('datasets.uploadingProgress', { pct: uploadProgress.pct })
                          : t('datasets.uploadVersion')}
                      </span>
                      <input
                        type="file"
                        accept=".jsonl,.csv,.parquet,.txt,.pdf,.md"
                        className="sr-only"
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          e.target.value = '';
                          if (file) void handleFileUpload(ds.id, file);
                        }}
                      />
                    </label>

                    {ds.latest_version && (
                      <>
                        <Button
                          variant="secondary"
                          onClick={() => {
                            setMappingNotice(null);
                            setMappingVersionId(ds.latest_version!.id);
                          }}
                          className="px-3 py-1.5 text-xs"
                        >
                          <Columns3 className="h-3.5 w-3.5" aria-hidden="true" />
                          <span>{t('datasets.columnMapping')}</span>
                        </Button>
                        <Button
                          variant="secondary"
                          onClick={() => setErrorsVersionId(ds.latest_version!.id)}
                          className="px-3 py-1.5 text-xs"
                        >
                          <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                          <span>{t('datasets.validationErrors')}</span>
                        </Button>
                        <Button
                          variant="secondary"
                          onClick={() => navigate(`/explorer?versionId=${ds.latest_version!.id}`)}
                          className="px-3 py-1.5 text-xs"
                        >
                          {t('datasets.openExplorer')}
                        </Button>
                        <Button
                          onClick={() => runMutation.mutate(ds.latest_version!.id)}
                          disabled={runMutation.isPending}
                          className="px-3 py-1.5 text-xs"
                        >
                          <Play className="h-3.5 w-3.5" aria-hidden="true" />
                          <span>{t('datasets.runQuality')}</span>
                        </Button>
                      </>
                    )}

                    <Button
                      variant="secondary"
                      onClick={() => {
                        setConnectorError(null);
                        setImportDatasetId(ds.id);
                      }}
                      className="px-3 py-1.5 text-xs"
                    >
                      <Plug className="h-3.5 w-3.5" aria-hidden="true" />
                      <span>{t('datasets.importConnector')}</span>
                    </Button>

                    {confirmDeleteId === ds.id ? (
                      <div className="flex items-center gap-1.5">
                        <Button
                          variant="destructive"
                          onClick={() => deleteDatasetMutation.mutate(ds.id)}
                          disabled={deleteDatasetMutation.isPending}
                          className="px-2.5 py-1.5 text-xs"
                        >
                          {t('datasets.confirmDeleteButton')}
                        </Button>
                        <Button
                          variant="ghost"
                          onClick={() => setConfirmDeleteId(null)}
                          className="px-2.5 py-1.5 text-xs"
                        >
                          {t('datasets.cancelDeleteButton')}
                        </Button>
                      </div>
                    ) : (
                      <Button
                        variant="destructive"
                        onClick={() => setConfirmDeleteId(ds.id)}
                        className="px-2.5 py-1.5 text-xs"
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                        <span>{t('datasets.deleteButton')}</span>
                      </Button>
                    )}
                  </div>
                </div>

                <p className="rounded-control border border-dashed border-border bg-canvas px-3 py-2 text-xs text-text-muted">
                  {uploadDatasetId === ds.id
                    ? t('datasets.createdPromptUpload')
                    : t('datasets.dropHint')}
                </p>
              </Card>
            );
          })}
        </div>
      )}

      {mappingVersionId && (
        <Card className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-text-primary">
              {t('datasets.mappingTitle')}
            </h2>
            <Button variant="ghost" onClick={() => setMappingVersionId(null)} className="px-2 py-1">
              <X className="h-4 w-4" aria-hidden="true" />
              <span className="sr-only">{t('datasets.close')}</span>
            </Button>
          </div>

          {mappingQuery.isLoading && <Skeleton className="h-24 w-full" />}
          {mappingQuery.data && mappingQuery.data.detected_columns.length === 0 && (
            <p className="text-xs text-text-muted">{t('datasets.mappingNoCols')}</p>
          )}
          {mappingQuery.data && mappingQuery.data.detected_columns.length > 0 && (
            <div className="space-y-3">
              <table className="w-full border-collapse text-left text-xs">
                <thead className="border-b border-border bg-subtle text-text-secondary">
                  <tr>
                    <th className="p-2">{t('datasets.mappingColSource')}</th>
                    <th className="p-2">{t('datasets.mappingColRole')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {mappingQuery.data.detected_columns.map((col) => (
                    <tr key={col}>
                      <td className="p-2 font-mono text-text-primary">{col}</td>
                      <td className="p-2">
                        <select
                          aria-label={col}
                          value={draftMapping[col] ?? ''}
                          onChange={(e) =>
                            setDraftMapping((prev) => ({ ...prev, [col]: e.target.value }))
                          }
                          className="rounded-control border border-border bg-surface px-2.5 py-1 text-xs text-text-primary"
                        >
                          <option value="">{t('datasets.mappingUnmapped')}</option>
                          <option value="prompt">prompt</option>
                          <option value="response">response</option>
                          <option value="chosen">chosen</option>
                          <option value="rejected">rejected</option>
                          <option value="messages">messages</option>
                          <option value="text">text</option>
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="flex items-center gap-3">
                <Button
                  onClick={() => saveMappingMutation.mutate()}
                  disabled={saveMappingMutation.isPending}
                  className="text-xs"
                >
                  {t('datasets.mappingSave')}
                </Button>
                {mappingNotice && <span className="text-xs text-success-600">{mappingNotice}</span>}
              </div>
            </div>
          )}
        </Card>
      )}

      {errorsVersionId && (
        <Card className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold text-text-primary">
              {t('datasets.errorsTitle', {
                count: formatNumber(errorsQuery.data?.invalid_row_count ?? 0, locale),
              })}
            </h2>
            <div className="flex items-center gap-2">
              {errorsQuery.data && errorsQuery.data.errors.length > 0 && (
                <>
                  <Button
                    variant="secondary"
                    onClick={() => downloadErrors('json')}
                    className="px-2.5 py-1 text-xs"
                  >
                    <Download className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>{t('datasets.downloadErrorsJson')}</span>
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => downloadErrors('csv')}
                    className="px-2.5 py-1 text-xs"
                  >
                    <Download className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>{t('datasets.downloadErrorsCsv')}</span>
                  </Button>
                </>
              )}
              <Button variant="ghost" onClick={() => setErrorsVersionId(null)} className="px-2 py-1">
                <X className="h-4 w-4" aria-hidden="true" />
                <span className="sr-only">{t('datasets.close')}</span>
              </Button>
            </div>
          </div>

          {errorsQuery.isLoading && <Skeleton className="h-24 w-full" />}
          {errorsQuery.data && errorsQuery.data.errors.length === 0 && (
            <p className="text-xs text-text-muted">{t('datasets.errorsEmpty')}</p>
          )}
          {errorsQuery.data && errorsQuery.data.errors.length > 0 && (
            <div className="max-h-64 overflow-auto">
              <table className="w-full border-collapse text-left text-xs">
                <thead className="sticky top-0 border-b border-border bg-subtle text-text-secondary">
                  <tr>
                    <th className="p-2">{t('datasets.errorLine')}</th>
                    <th className="p-2">{t('datasets.errorType')}</th>
                    <th className="p-2">{t('datasets.errorMessage')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {errorsQuery.data.errors.map((err, i) => (
                    <tr key={`${err.line_number}-${i}`}>
                      <td className="p-2 font-mono tabular-nums text-text-muted">
                        {err.line_number}
                      </td>
                      <td className="p-2 font-mono text-danger-600">{err.error_type}</td>
                      <td className="p-2 text-text-primary">{err.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {importDatasetId && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={t('datasets.connectorModalTitle')}
          className="fixed inset-0 z-50 flex items-center justify-center bg-canvas/80 p-4 backdrop-blur-sm"
        >
          <Card className="w-full max-w-lg space-y-4 p-5">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold text-text-primary">
                {t('datasets.connectorModalTitle')}
              </h2>
              <Button
                variant="ghost"
                onClick={() => setImportDatasetId(null)}
                className="px-2 py-1"
              >
                <X className="h-4 w-4" aria-hidden="true" />
                <span className="sr-only">{t('datasets.close')}</span>
              </Button>
            </div>

            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (!connectorUri.trim()) return;
                connectorImportMutation.mutate();
              }}
              className="space-y-3"
            >
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="conn-type"
                    className="mb-1 block text-xs font-medium text-text-primary"
                  >
                    {t('datasets.connectorTypeLabel')}
                  </label>
                  <select
                    id="conn-type"
                    value={connectorType}
                    onChange={(e) =>
                      setConnectorType(e.target.value as 's3' | 'database' | 'sharepoint')
                    }
                    className="w-full rounded-control border border-border bg-surface px-3 py-2 text-sm text-text-primary"
                  >
                    <option value="s3">S3 / MinIO</option>
                    <option value="database">SQL Database (Read-Only)</option>
                    <option value="sharepoint">SharePoint HTTPS</option>
                  </select>
                </div>

                <div>
                  <label
                    htmlFor="conn-format"
                    className="mb-1 block text-xs font-medium text-text-primary"
                  >
                    {t('datasets.connectorFormatLabel')}
                  </label>
                  <select
                    id="conn-format"
                    value={connectorType === 'database' ? 'jsonl' : connectorFormat}
                    disabled={connectorType === 'database'}
                    onChange={(e) =>
                      setConnectorFormat(
                        e.target.value as 'jsonl' | 'csv' | 'parquet' | 'txt' | 'pdf' | 'md',
                      )
                    }
                    className="w-full rounded-control border border-border bg-surface px-3 py-2 text-sm text-text-primary"
                  >
                    <option value="jsonl">JSONL</option>
                    <option value="csv">CSV</option>
                    <option value="parquet">Parquet</option>
                    <option value="txt">TXT</option>
                    <option value="md">Markdown</option>
                    <option value="pdf">PDF</option>
                  </select>
                </div>
              </div>

              <div>
                <label
                  htmlFor="conn-uri"
                  className="mb-1 block text-xs font-medium text-text-primary"
                >
                  {t('datasets.connectorUriLabel')}
                </label>
                <Input
                  id="conn-uri"
                  placeholder="s3://bucket/path.jsonl atau sqlite:///path/to/db"
                  value={connectorUri}
                  onChange={(e) => setConnectorUri(e.target.value)}
                  required
                />
              </div>

              {connectorType === 'database' && (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <label
                      htmlFor="conn-table"
                      className="mb-1 block text-xs font-medium text-text-primary"
                    >
                      {t('datasets.connectorTableLabel')}
                    </label>
                    <Input
                      id="conn-table"
                      placeholder="prompts_table"
                      value={connectorTable}
                      onChange={(e) => setConnectorTable(e.target.value)}
                    />
                  </div>
                  <div>
                    <label
                      htmlFor="conn-query"
                      className="mb-1 block text-xs font-medium text-text-primary"
                    >
                      {t('datasets.connectorQueryLabel')}
                    </label>
                    <Input
                      id="conn-query"
                      placeholder="SELECT prompt, response FROM ..."
                      value={connectorQuery}
                      onChange={(e) => setConnectorQuery(e.target.value)}
                    />
                  </div>
                </div>
              )}

              {connectorType === 'sharepoint' && (
                <div>
                  <label
                    htmlFor="conn-cred"
                    className="mb-1 block text-xs font-medium text-text-primary"
                  >
                    {t('datasets.connectorCredRefLabel')}
                  </label>
                  <Input
                    id="conn-cred"
                    placeholder="QUALITEXT_SHAREPOINT_TOKEN"
                    value={connectorCredRef}
                    onChange={(e) => setConnectorCredRef(e.target.value)}
                  />
                </div>
              )}

              {connectorError && (
                <p role="alert" className="text-xs text-danger-600">
                  {connectorError}
                </p>
              )}

              <div className="flex justify-end gap-2 pt-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setImportDatasetId(null)}
                >
                  {t('datasets.cancelDeleteButton')}
                </Button>
                <Button type="submit" disabled={connectorImportMutation.isPending}>
                  {t('datasets.connectorSubmit')}
                </Button>
              </div>
            </form>
          </Card>
        </div>
      )}
    </div>
  );
}
