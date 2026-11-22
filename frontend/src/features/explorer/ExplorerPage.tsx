import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  Eye,
  Flag,
  Hash,
  Keyboard,
  Pencil,
  ShieldAlert,
  Sparkles,
  TableProperties,
  Trash2,
  X,
} from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Skeleton } from '@/components/ui/Skeleton';
import { DiffViewer } from '@/components/shared/DiffViewer';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { HighlightedText } from '@/components/shared/HighlightedText';
import { VirtualizedDataTable, type VirtualColumn } from '@/components/shared/VirtualizedDataTable';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';
import { formatNumber, useTranslation } from '@/lib/i18n';

interface RowItem {
  row_id: string;
  line_number: number;
  prompt: string;
  response: string;
  text: string;
  is_valid: boolean;
  validation_error: string;
  curation_status: string;
  has_pii: boolean;
}

interface PaginatedRows {
  version_id: string;
  total: number;
  limit: number;
  offset: number;
  pii_revealed: boolean;
  items: RowItem[];
}

type CurationActionType =
  | 'approve'
  | 'reject'
  | 'flag'
  | 'mask_pii'
  | 'hash_pii'
  | 'drop_row';

type ExportFormat = 'jsonl' | 'csv' | 'parquet' | 'huggingface';

export function ExplorerPage() {
  const { accessToken } = useAuthStore();
  const { t, locale } = useTranslation();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const versionId = searchParams.get('versionId');

  const [search, setSearch] = useState('');
  const [curationStatus, setCurationStatus] = useState('');
  const [onlyInvalid, setOnlyInvalid] = useState(false);
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(50);
  const [density, setDensity] = useState<'comfortable' | 'compact'>('comfortable');
  const [revealPii, setRevealPii] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [activeRowId, setActiveRowId] = useState<string | null>(null);
  const [exportFormat, setExportFormat] = useState<ExportFormat>('jsonl');
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const [editingRow, setEditingRow] = useState<RowItem | null>(null);
  const [draftPrompt, setDraftPrompt] = useState('');
  const [draftResponse, setDraftResponse] = useState('');
  const [draftText, setDraftText] = useState('');
  const [aiMode, setAiMode] = useState<string | null>(null);
  const editingRowIdRef = useRef<string | null>(null);
  editingRowIdRef.current = editingRow?.row_id ?? null;
  const isStructuredRow = Boolean(editingRow && (editingRow.prompt || editingRow.response));

  useEffect(() => {
    setActiveRowId(null);
    setSelectedIds([]);
    setDiffResult(null);
    setEditingRow(null);
    setOffset(0);
    setRevealPii(false);
  }, [versionId]);

  const [diffPiiMode, setDiffPiiMode] = useState<'mask' | 'hash'>('mask');
  const [diffCleanArtifacts, setDiffCleanArtifacts] = useState(true);
  const [diffResult, setDiffResult] = useState<{
    before: string;
    after: string;
    changed: boolean;
  } | null>(null);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [
      'explorer-rows',
      versionId,
      search,
      curationStatus,
      onlyInvalid,
      revealPii,
      limit,
      offset,
    ],
    enabled: Boolean(versionId && accessToken),
    queryFn: () => {
      const params = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
      });
      if (search) params.set('search', search);
      if (curationStatus) params.set('curation_status', curationStatus);
      if (onlyInvalid) params.set('only_invalid', 'true');
      if (revealPii) params.set('reveal_pii', 'true');
      return apiFetch<PaginatedRows>(
        `/versions/${versionId}/rows?${params.toString()}`,
        { method: 'GET' },
        accessToken,
      );
    },
  });

  const activeRow = useMemo(() => {
    if (!data?.items.length) return null;
    if (activeRowId) {
      return data.items.find((r) => r.row_id === activeRowId) ?? null;
    }
    return null;
  }, [data, activeRowId]);

  useEffect(() => {
    if (error instanceof ApiError && error.code === 'FORBIDDEN_PII_VIEW') {
      setFeedback(error.message);
      setRevealPii(false);
    }
  }, [error]);

  const curateMutation = useMutation({
    mutationFn: ({ action, rowIds }: { action: CurationActionType; rowIds?: string[] }) => {
      const ids = rowIds ?? selectedIds;
      return apiFetch<{ id: string; version_number: number }>(
        `/versions/${versionId}/curation`,
        {
          method: 'POST',
          body: JSON.stringify({
            actions: ids.map((row_id) => ({ row_id, action })),
          }),
        },
        accessToken,
      );
    },
    onSuccess: (newVer) => {
      setSelectedIds([]);
      setOffset(0);
      setFeedback(t('explorer.curateSuccess', { version: newVer.version_number }));
      navigate(`/explorer?versionId=${newVer.id}`);
      queryClient.invalidateQueries({ queryKey: ['explorer-rows'] });
    },
    onError: (err) => {
      setFeedback(err instanceof ApiError ? err.message : t('explorer.curateError'));
    },
  });

  const exportMutation = useMutation({
    mutationFn: async (format: ExportFormat) => {
      const exp = await apiFetch<{ id: string; export_format: string }>(
        `/versions/${versionId}/exports`,
        { method: 'POST', body: JSON.stringify({ export_format: format }) },
        accessToken,
      );
      const base = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1';
      const dlRes = await fetch(`${base}/exports/${exp.id}/download`, {
        method: 'GET',
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      });
      if (!dlRes.ok) {
        throw new ApiError('EXPORT_DOWNLOAD_FAILED', t('explorer.exportError'), dlRes.status);
      }
      const blob = await dlRes.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      const ext = format === 'huggingface' ? 'hf.zip' : format;
      anchor.href = url;
      anchor.download = `qualitext_export.${ext}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      return exp;
    },
    onSuccess: (exp) => {
      setFeedback(
        t('explorer.exportSuccess', {
          format: exp.export_format.toUpperCase(),
          id: exp.id,
        }),
      );
    },
    onError: (err) => {
      setFeedback(err instanceof ApiError ? err.message : t('explorer.exportError'));
    },
  });

  const diffPreviewMutation = useMutation({
    mutationFn: (text: string) =>
      apiFetch<{ before: string; after: string; changed: boolean }>(
        '/curation/diff-preview',
        {
          method: 'POST',
          body: JSON.stringify({
            text,
            pii_mode: diffPiiMode,
            clean_artifacts: diffCleanArtifacts,
          }),
        },
        accessToken,
      ),
    onSuccess: (res) => {
      setDiffResult(res);
    },
  });

  const aiRepairMutation = useMutation({
    mutationFn: ({
      text,
    }: {
      rowId: string;
      field: 'prompt' | 'response' | 'text';
      text: string;
    }) =>
      apiFetch<{ before: string; after: string; changed: boolean; mode: string }>(
        '/curation/ai-repair-preview',
        { method: 'POST', body: JSON.stringify({ text }) },
        accessToken,
      ),
    onSuccess: (res, variables) => {
      if (editingRowIdRef.current !== variables.rowId) return;
      if (variables.field === 'prompt') {
        setDraftPrompt(res.after);
      } else if (variables.field === 'response') {
        setDraftResponse(res.after);
      } else {
        setDraftText(res.after);
      }
      setAiMode(res.mode);
    },
    onError: (err) => {
      setFeedback(err instanceof ApiError ? err.message : t('explorer.aiRepairError'));
    },
  });

  const saveEditMutation = useMutation({
    mutationFn: () => {
      const actionPayload = isStructuredRow
        ? {
            row_id: editingRow!.row_id,
            action: 'edit' as const,
            prompt: draftPrompt,
            response: draftResponse,
          }
        : {
            row_id: editingRow!.row_id,
            action: 'edit' as const,
            text: draftText,
          };
      return apiFetch<{ id: string; version_number: number }>(
        `/versions/${versionId}/curation`,
        {
          method: 'POST',
          body: JSON.stringify({ actions: [actionPayload] }),
        },
        accessToken,
      );
    },
    onSuccess: (newVer) => {
      setEditingRow(null);
      setAiMode(null);
      setFeedback(t('explorer.repairSuccess', { version: newVer.version_number }));
      navigate(`/explorer?versionId=${newVer.id}`);
      queryClient.invalidateQueries({ queryKey: ['explorer-rows'] });
    },
    onError: (err) => {
      setFeedback(err instanceof ApiError ? err.message : t('explorer.repairError'));
    },
  });

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        if (showShortcuts) {
          setShowShortcuts(false);
          return;
        }
        if (editingRow) {
          setEditingRow(null);
          return;
        }
      }
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable)
      ) {
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) {
        return;
      }
      if (e.key === '?') {
        e.preventDefault();
        setShowShortcuts((v) => !v);
        return;
      }
      const items = data?.items ?? [];
      if (items.length === 0) return;

      const lower = e.key.toLowerCase();
      if (lower === 'j' || lower === 'k') {
        e.preventDefault();
        const curIdx = items.findIndex((r) => r.row_id === activeRowId);
        const nextIdx =
          curIdx === -1
            ? 0
            : lower === 'j'
              ? Math.min(items.length - 1, curIdx + 1)
              : Math.max(0, curIdx - 1);
        setActiveRowId(items[nextIdx].row_id);
        setDiffResult(null);
        return;
      }

      if (!activeRowId || curateMutation.isPending) return;
      if (lower === 'a') {
        e.preventDefault();
        curateMutation.mutate({ action: 'approve', rowIds: [activeRowId] });
      } else if (lower === 'r') {
        e.preventDefault();
        curateMutation.mutate({ action: 'reject', rowIds: [activeRowId] });
      } else if (lower === 'f') {
        e.preventDefault();
        curateMutation.mutate({ action: 'flag', rowIds: [activeRowId] });
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeRowId, curateMutation, data?.items, editingRow, showShortcuts]);

  if (!versionId) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold text-text-primary">{t('explorer.title')}</h1>
        <EmptyState
          icon={<TableProperties className="h-10 w-10" />}
          title={t('explorer.emptyTitle')}
          description={t('explorer.emptyDescription')}
        />
      </div>
    );
  }

  function toggleRow(id: string) {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  }

  const columns: VirtualColumn<RowItem>[] = [
    {
      key: 'select',
      header: t('explorer.colSelect'),
      render: (row) => (
        <input
          type="checkbox"
          aria-label={`${t('explorer.colSelect')} ${row.row_id}`}
          checked={selectedIds.includes(row.row_id)}
          onClick={(e) => e.stopPropagation()}
          onChange={() => toggleRow(row.row_id)}
        />
      ),
    },
    {
      key: 'row_id',
      header: t('explorer.colRowId'),
      className: 'font-mono text-text-muted',
      render: (row) => row.row_id,
    },
    {
      key: 'prompt',
      header: t('explorer.colPrompt'),
      className: 'max-w-md truncate font-mono text-text-primary',
      render: (row) => row.prompt || row.text || '—',
    },
    {
      key: 'response',
      header: t('explorer.colResponse'),
      className: 'max-w-md truncate font-mono text-text-secondary',
      render: (row) => row.response || '—',
    },
    {
      key: 'status',
      header: t('explorer.colStatus'),
      render: (row) => (
        <div className="flex items-center gap-1.5">
          <Badge
            tone={
              row.curation_status === 'approved'
                ? 'success'
                : row.curation_status === 'rejected'
                  ? 'danger'
                  : row.curation_status === 'flagged'
                    ? 'warning'
                    : 'neutral'
            }
          >
            {row.curation_status}
          </Badge>
          {row.has_pii && <Badge tone="danger">PII</Badge>}
        </div>
      ),
    },
    {
      key: 'action',
      header: t('explorer.colAction'),
      render: (row) => (
        <Button
          variant="ghost"
          className="px-2 py-1 text-xs"
          onClick={(e) => {
            e.stopPropagation();
            setEditingRow(row);
            setDraftPrompt(row.prompt || '');
            setDraftResponse(row.response || '');
            setDraftText(row.text || row.prompt || '');
            setAiMode(null);
          }}
        >
          <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
          <span>{t('explorer.btnFix')}</span>
        </Button>
      ),
    },
  ];

  const totalRows = data?.total ?? 0;
  const pageStart = totalRows === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(totalRows, offset + (data?.items.length ?? 0));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">{t('explorer.title')}</h1>
          <p className="font-mono text-xs text-text-muted">Version ID: {versionId}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="ghost"
            onClick={() => setShowShortcuts((v) => !v)}
            className="px-2.5 py-1.5 text-xs"
          >
            <Keyboard className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{t('explorer.shortcutsBtn')}</span>
          </Button>
          <Button
            variant="secondary"
            onClick={() => setRevealPii((v) => !v)}
            className="px-3 py-1.5 text-xs"
          >
            <Eye className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{revealPii ? t('explorer.maskPiiView') : t('explorer.revealPii')}</span>
          </Button>
          <select
            aria-label={t('explorer.exportFormatLabel')}
            value={exportFormat}
            onChange={(e) => setExportFormat(e.target.value as ExportFormat)}
            className="rounded-control border border-border bg-surface px-2.5 py-1.5 text-xs text-text-primary"
          >
            <option value="jsonl">JSONL</option>
            <option value="csv">CSV</option>
            <option value="parquet">Parquet</option>
            <option value="huggingface">Hugging Face (.zip)</option>
          </select>
          <Button
            variant="secondary"
            onClick={() => exportMutation.mutate(exportFormat)}
            disabled={exportMutation.isPending}
            className="px-3 py-1.5 text-xs"
          >
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{t('explorer.exportButton')}</span>
          </Button>
        </div>
      </div>

      <Card className="space-y-3 p-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              placeholder={t('explorer.searchPlaceholder')}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setOffset(0);
              }}
              className="max-w-xs"
            />
            <select
              aria-label={t('explorer.colStatus')}
              value={curationStatus}
              onChange={(e) => {
                setCurationStatus(e.target.value);
                setOffset(0);
              }}
              className="rounded-control border border-border bg-surface px-2.5 py-2 text-xs text-text-primary"
            >
              <option value="">{t('explorer.statusAll')}</option>
              <option value="pending">{t('explorer.statusPending')}</option>
              <option value="approved">{t('explorer.statusApproved')}</option>
              <option value="flagged">{t('explorer.statusFlagged')}</option>
              <option value="rejected">{t('explorer.statusRejected')}</option>
            </select>
            <label className="flex items-center gap-1.5 text-xs text-text-secondary">
              <input
                type="checkbox"
                checked={onlyInvalid}
                onChange={(e) => {
                  setOnlyInvalid(e.target.checked);
                  setOffset(0);
                }}
              />
              <span>{t('explorer.onlyInvalid')}</span>
            </label>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              onClick={() =>
                setDensity((d) => (d === 'comfortable' ? 'compact' : 'comfortable'))
              }
              className="px-2.5 py-1.5 text-xs"
            >
              {density === 'comfortable'
                ? t('explorer.densityComfortable')
                : t('explorer.densityCompact')}
            </Button>
          </div>
        </div>

        {selectedIds.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
            <span className="text-xs text-text-secondary">
              {t('explorer.selectedCount', { count: selectedIds.length })}
            </span>
            <Button
              variant="secondary"
              onClick={() => curateMutation.mutate({ action: 'approve' })}
              className="px-2.5 py-1 text-xs"
            >
              <Check className="h-3.5 w-3.5" aria-hidden="true" /> {t('explorer.actionApprove')}
            </Button>
            <Button
              variant="secondary"
              onClick={() => curateMutation.mutate({ action: 'flag' })}
              className="px-2.5 py-1 text-xs"
            >
              <Flag className="h-3.5 w-3.5" aria-hidden="true" /> {t('explorer.actionFlag')}
            </Button>
            <Button
              variant="secondary"
              onClick={() => curateMutation.mutate({ action: 'mask_pii' })}
              className="px-2.5 py-1 text-xs"
            >
              <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" />{' '}
              {t('explorer.actionMaskPii')}
            </Button>
            <Button
              variant="secondary"
              onClick={() => curateMutation.mutate({ action: 'hash_pii' })}
              className="px-2.5 py-1 text-xs"
            >
              <Hash className="h-3.5 w-3.5" aria-hidden="true" /> {t('explorer.actionHashPii')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => curateMutation.mutate({ action: 'drop_row' })}
              className="px-2.5 py-1 text-xs"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" /> {t('explorer.actionDropRow')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => curateMutation.mutate({ action: 'reject' })}
              className="px-2.5 py-1 text-xs"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" /> {t('explorer.actionReject')}
            </Button>
          </div>
        )}
      </Card>

      {feedback && (
        <div className="rounded-control border border-border bg-surface p-3 text-xs text-text-primary">
          {feedback}
        </div>
      )}

      {showShortcuts && (
        <Card role="dialog" aria-label={t('explorer.shortcutsTitle')} className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-text-primary">
              {t('explorer.shortcutsTitle')}
            </h2>
            <Button
              variant="ghost"
              onClick={() => setShowShortcuts(false)}
              className="px-2 py-1 text-xs"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
          <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
            <div className="flex items-center justify-between rounded-control bg-subtle px-2.5 py-1.5">
              <dt className="font-mono font-semibold text-text-primary">J / K</dt>
              <dd className="text-text-secondary">{t('explorer.shortcutNextPrev')}</dd>
            </div>
            <div className="flex items-center justify-between rounded-control bg-subtle px-2.5 py-1.5">
              <dt className="font-mono font-semibold text-text-primary">A</dt>
              <dd className="text-text-secondary">{t('explorer.shortcutApprove')}</dd>
            </div>
            <div className="flex items-center justify-between rounded-control bg-subtle px-2.5 py-1.5">
              <dt className="font-mono font-semibold text-text-primary">R</dt>
              <dd className="text-text-secondary">{t('explorer.shortcutReject')}</dd>
            </div>
            <div className="flex items-center justify-between rounded-control bg-subtle px-2.5 py-1.5">
              <dt className="font-mono font-semibold text-text-primary">F</dt>
              <dd className="text-text-secondary">{t('explorer.shortcutFlag')}</dd>
            </div>
            <div className="flex items-center justify-between rounded-control bg-subtle px-2.5 py-1.5">
              <dt className="font-mono font-semibold text-text-primary">?</dt>
              <dd className="text-text-secondary">{t('explorer.shortcutHelp')}</dd>
            </div>
          </dl>
        </Card>
      )}

      {isLoading && <Skeleton className="h-64 w-full" />}
      {error && (
        <ErrorState
          message={error instanceof ApiError ? error.message : t('explorer.loadError')}
          onRetry={() => refetch()}
        />
      )}

      {data && (
        <>
          <VirtualizedDataTable
            rows={data.items}
            getRowId={(row) => row.row_id}
            columns={columns}
            density={density}
            activeRowId={activeRowId}
            onRowClick={(row) => {
              setActiveRowId(row.row_id);
              setDiffResult(null);
            }}
          />

          <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-text-secondary">
            <span className="tabular-nums">
              {t('explorer.pageSummary', {
                start: formatNumber(pageStart, locale),
                end: formatNumber(pageEnd, locale),
                total: formatNumber(totalRows, locale),
              })}
            </span>

            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5">
                <span>{t('explorer.perPage')}</span>
                <select
                  aria-label={t('explorer.perPage')}
                  value={limit}
                  onChange={(e) => {
                    setLimit(Number(e.target.value));
                    setOffset(0);
                  }}
                  className="rounded-control border border-border bg-surface px-2 py-1 text-xs text-text-primary"
                >
                  <option value={25}>25</option>
                  <option value={50}>50</option>
                  <option value={100}>100</option>
                  <option value={200}>200</option>
                </select>
              </label>

              <Button
                variant="secondary"
                disabled={offset === 0}
                onClick={() => setOffset((prev) => Math.max(0, prev - limit))}
                className="px-2.5 py-1 text-xs"
              >
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{t('explorer.prevPage')}</span>
              </Button>
              <Button
                variant="secondary"
                disabled={offset + limit >= totalRows}
                onClick={() => setOffset((prev) => prev + limit)}
                className="px-2.5 py-1 text-xs"
              >
                <span>{t('explorer.nextPage')}</span>
                <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
            </div>
          </div>
        </>
      )}

      {activeRow && (
        <Card className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold text-text-primary">
                {t('explorer.detailTitle')} — <span className="font-mono">{activeRow.row_id}</span>
              </h2>
              <p className="text-xs text-text-muted">{t('explorer.detailHighlighted')}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select
                aria-label={t('explorer.piiModeLabel')}
                value={diffPiiMode}
                onChange={(e) => setDiffPiiMode(e.target.value as 'mask' | 'hash')}
                className="rounded-control border border-border bg-surface px-2 py-1 text-xs text-text-primary"
              >
                <option value="mask">mask</option>
                <option value="hash">hash</option>
              </select>
              <label className="flex items-center gap-1.5 text-xs text-text-secondary">
                <input
                  type="checkbox"
                  checked={diffCleanArtifacts}
                  onChange={(e) => setDiffCleanArtifacts(e.target.checked)}
                />
                <span>{t('explorer.cleanArtifactsLabel')}</span>
              </label>
              <Button
                variant="secondary"
                onClick={() => diffPreviewMutation.mutate(activeRow.text || activeRow.prompt)}
                disabled={diffPreviewMutation.isPending}
                className="px-2.5 py-1 text-xs"
              >
                {t('explorer.previewDiffBtn')}
              </Button>
            </div>
          </div>

          <div className="rounded-control border border-border bg-canvas p-3">
            <HighlightedText text={activeRow.text || activeRow.prompt} />
          </div>

          {diffResult && (
            <DiffViewer
              before={diffResult.before}
              after={diffResult.after}
              changed={diffResult.changed}
            />
          )}
        </Card>
      )}

      {editingRow && (
        <Card className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-text-primary">
                {t('explorer.editTitle', { rowId: editingRow.row_id })}
              </h2>
              <p className="text-xs text-text-muted">{t('explorer.editSubtitle')}</p>
            </div>
            {aiMode && <Badge tone="info">Mode: {aiMode}</Badge>}
          </div>

          {isStructuredRow ? (
            <div className="space-y-3">
              <div>
                <label
                  htmlFor="edit-prompt"
                  className="mb-1 block text-xs font-medium text-text-muted"
                >
                  {t('explorer.promptLabel')}
                </label>
                <textarea
                  id="edit-prompt"
                  rows={3}
                  value={draftPrompt}
                  onChange={(e) => setDraftPrompt(e.target.value)}
                  className="w-full rounded-control border border-border bg-canvas p-3 font-mono text-xs text-text-primary focus:border-brand-600 focus:outline-none"
                />
              </div>
              <div>
                <label
                  htmlFor="edit-response"
                  className="mb-1 block text-xs font-medium text-text-muted"
                >
                  {t('explorer.responseLabel')}
                </label>
                <textarea
                  id="edit-response"
                  rows={4}
                  value={draftResponse}
                  onChange={(e) => setDraftResponse(e.target.value)}
                  className="w-full rounded-control border border-border bg-canvas p-3 font-mono text-xs text-text-primary focus:border-brand-600 focus:outline-none"
                />
              </div>
            </div>
          ) : (
            <div>
              <label htmlFor="edit-text" className="mb-1 block text-xs font-medium text-text-muted">
                {t('explorer.textLabel')}
              </label>
              <textarea
                id="edit-text"
                rows={5}
                value={draftText}
                onChange={(e) => setDraftText(e.target.value)}
                className="w-full rounded-control border border-border bg-canvas p-3 font-mono text-xs text-text-primary focus:border-brand-600 focus:outline-none"
              />
            </div>
          )}

          <DiffViewer
            before={
              isStructuredRow
                ? `${editingRow.prompt}\n${editingRow.response}`.trim()
                : editingRow.text || editingRow.prompt
            }
            after={
              isStructuredRow ? `${draftPrompt}\n${draftResponse}`.trim() : draftText
            }
          />

          <div className="flex flex-wrap items-center gap-2">
            {isStructuredRow ? (
              <>
                <Button
                  variant="secondary"
                  onClick={() =>
                    aiRepairMutation.mutate({
                      rowId: editingRow.row_id,
                      field: 'prompt',
                      text: draftPrompt,
                    })
                  }
                  disabled={aiRepairMutation.isPending}
                  className="text-xs"
                >
                  <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{t('explorer.aiSuggestPrompt')}</span>
                </Button>
                <Button
                  variant="secondary"
                  onClick={() =>
                    aiRepairMutation.mutate({
                      rowId: editingRow.row_id,
                      field: 'response',
                      text: draftResponse,
                    })
                  }
                  disabled={aiRepairMutation.isPending}
                  className="text-xs"
                >
                  <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{t('explorer.aiSuggestResponse')}</span>
                </Button>
              </>
            ) : (
              <Button
                variant="secondary"
                onClick={() =>
                  aiRepairMutation.mutate({
                    rowId: editingRow.row_id,
                    field: 'text',
                    text: draftText,
                  })
                }
                disabled={aiRepairMutation.isPending}
                className="text-xs"
              >
                <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{t('explorer.aiSuggest')}</span>
              </Button>
            )}
            <Button
              onClick={() => saveEditMutation.mutate()}
              disabled={saveEditMutation.isPending}
              className="text-xs"
            >
              {t('explorer.saveNewVersion')}
            </Button>
            <Button variant="ghost" onClick={() => setEditingRow(null)} className="text-xs">
              {t('explorer.cancel')}
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}
