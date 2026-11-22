import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Skeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';
import { formatDate, useTranslation } from '@/lib/i18n';

interface ApiTokenItem {
  id: string;
  name: string;
  scopes: string[];
  expires_at: string | null;
  is_revoked: boolean;
  created_at: string;
}

interface CreatedTokenResponse {
  id: string;
  name: string;
  token: string;
  scopes: string[];
}

interface WebhookItem {
  id: string;
  name: string;
  url: string;
  events: string[];
  is_active: boolean;
  created_at: string;
}

interface CreatedWebhookResponse {
  id: string;
  name: string;
  signing_secret: string;
}

interface SsoProviders {
  oidc_enabled: boolean;
  oidc_provider_name: string;
  saml_enabled: boolean;
}

interface BenchmarkSetItem {
  id: string;
  workspace_id: string;
  name: string;
  description: string;
  entry_count: number;
  is_active: boolean;
  created_at: string;
}

interface RetentionPolicyItem {
  workspace_id: string;
  default_retention_days: number | null;
}

interface RetentionPurgeItem {
  workspace_id: string;
  purged_datasets: number;
  purged_versions: number;
}

const ALL_SCOPES = [
  'dataset:read',
  'dataset:write',
  'run:execute',
  'export:create',
  'pii:view',
  'policy:manage',
  'audit:read',
] as const;

const ALL_WEBHOOK_EVENTS = ['run.completed', 'run.failed', 'export.completed'] as const;

export function SettingsPage() {
  const { accessToken, activeWorkspaceId } = useAuthStore();
  const { t, locale } = useTranslation();
  const queryClient = useQueryClient();

  const [tab, setTab] = useState<'tokens' | 'webhooks' | 'sso' | 'benchmarks' | 'retention'>(
    'tokens',
  );
  const [tokenName, setTokenName] = useState('');
  const [tokenScopes, setTokenScopes] = useState<string[]>(['dataset:read', 'export:create']);
  const [revealedToken, setRevealedToken] = useState<string | null>(null);
  const [tokenError, setTokenError] = useState<string | null>(null);

  const [hookName, setHookName] = useState('');
  const [hookUrl, setHookUrl] = useState('');
  const [hookEvents, setHookEvents] = useState<string[]>(['run.completed', 'export.completed']);
  const [revealedSecret, setRevealedSecret] = useState<string | null>(null);
  const [webhookStatus, setWebhookStatus] = useState<string | null>(null);
  const [webhookError, setWebhookError] = useState<string | null>(null);

  const [benchName, setBenchName] = useState('');
  const [benchDesc, setBenchDesc] = useState('');
  const [benchPromptsText, setBenchPromptsText] = useState('');
  const [benchStatus, setBenchStatus] = useState<string | null>(null);
  const [benchError, setBenchError] = useState<string | null>(null);

  const [retentionDaysInput, setRetentionDaysInput] = useState('');
  const [retentionStatus, setRetentionStatus] = useState<string | null>(null);
  const [retentionError, setRetentionError] = useState<string | null>(null);

  const tokensQuery = useQuery({
    queryKey: ['api-tokens'],
    enabled: Boolean(accessToken && tab === 'tokens'),
    queryFn: () => apiFetch<ApiTokenItem[]>('/api-tokens', { method: 'GET' }, accessToken),
  });

  const webhooksQuery = useQuery({
    queryKey: ['webhooks', activeWorkspaceId],
    enabled: Boolean(accessToken && activeWorkspaceId && tab === 'webhooks'),
    queryFn: () =>
      apiFetch<WebhookItem[]>(
        `/workspaces/${activeWorkspaceId}/webhooks`,
        { method: 'GET' },
        accessToken,
      ),
  });

  const ssoQuery = useQuery({
    queryKey: ['sso-providers'],
    enabled: tab === 'sso',
    queryFn: () => apiFetch<SsoProviders>('/auth/sso/providers', { method: 'GET' }),
  });

  const benchmarksQuery = useQuery({
    queryKey: ['benchmarks', activeWorkspaceId],
    enabled: Boolean(accessToken && activeWorkspaceId && tab === 'benchmarks'),
    queryFn: () =>
      apiFetch<BenchmarkSetItem[]>(
        `/workspaces/${activeWorkspaceId}/benchmarks`,
        { method: 'GET' },
        accessToken,
      ),
  });

  const retentionQuery = useQuery({
    queryKey: ['retention', activeWorkspaceId],
    enabled: Boolean(accessToken && activeWorkspaceId && tab === 'retention'),
    queryFn: () =>
      apiFetch<RetentionPolicyItem>(
        `/workspaces/${activeWorkspaceId}/retention`,
        { method: 'GET' },
        accessToken,
      ),
  });

  useEffect(() => {
    if (retentionQuery.data) {
      setRetentionDaysInput(
        retentionQuery.data.default_retention_days
          ? String(retentionQuery.data.default_retention_days)
          : '',
      );
    }
  }, [retentionQuery.data]);

  const createTokenMut = useMutation({
    mutationFn: () =>
      apiFetch<CreatedTokenResponse>(
        '/api-tokens',
        {
          method: 'POST',
          body: JSON.stringify({ name: tokenName.trim(), scopes: tokenScopes }),
        },
        accessToken,
      ),
    onSuccess: (res) => {
      setTokenError(null);
      setTokenName('');
      setRevealedToken(res.token);
      void queryClient.invalidateQueries({ queryKey: ['api-tokens'] });
    },
    onError: (err) => {
      setTokenError(err instanceof ApiError ? err.message : t('settings.tokenError'));
    },
  });

  const revokeTokenMut = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(`/api-tokens/${id}`, { method: 'DELETE' }, accessToken),
    onSuccess: () => {
      setTokenError(null);
      void queryClient.invalidateQueries({ queryKey: ['api-tokens'] });
    },
    onError: (err) => {
      setTokenError(err instanceof ApiError ? err.message : t('settings.tokenError'));
    },
  });

  const createWebhookMut = useMutation({
    mutationFn: () =>
      apiFetch<CreatedWebhookResponse>(
        `/workspaces/${activeWorkspaceId}/webhooks`,
        {
          method: 'POST',
          body: JSON.stringify({
            name: hookName.trim(),
            url: hookUrl.trim(),
            events: hookEvents,
          }),
        },
        accessToken,
      ),
    onSuccess: (res) => {
      setWebhookError(null);
      setHookName('');
      setHookUrl('');
      setRevealedSecret(res.signing_secret);
      void queryClient.invalidateQueries({ queryKey: ['webhooks', activeWorkspaceId] });
    },
    onError: (err) => {
      setWebhookError(err instanceof ApiError ? err.message : t('settings.webhookError'));
    },
  });

  const deleteWebhookMut = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(
        `/workspaces/${activeWorkspaceId}/webhooks/${id}`,
        { method: 'DELETE' },
        accessToken,
      ),
    onSuccess: () => {
      setWebhookError(null);
      void queryClient.invalidateQueries({ queryKey: ['webhooks', activeWorkspaceId] });
    },
    onError: (err) => {
      setWebhookError(err instanceof ApiError ? err.message : t('settings.webhookError'));
    },
  });

  const testWebhookMut = useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ delivered: boolean; status_code: number | null; error_message: string | null }>(
        `/workspaces/${activeWorkspaceId}/webhooks/${id}/test`,
        { method: 'POST' },
        accessToken,
      ),
    onSuccess: (res) => {
      if (res.delivered) {
        setWebhookError(null);
        setWebhookStatus(t('settings.webhookTestSuccess', { status: res.status_code ?? 200 }));
      } else {
        setWebhookStatus(null);
        setWebhookError(
          t('settings.webhookTestFailed', { error: res.error_message ?? 'unknown error' }),
        );
      }
    },
    onError: (err) => {
      setWebhookStatus(null);
      setWebhookError(err instanceof ApiError ? err.message : t('settings.webhookError'));
    },
  });

  const createBenchmarkMut = useMutation({
    mutationFn: () =>
      apiFetch<BenchmarkSetItem>(
        `/workspaces/${activeWorkspaceId}/benchmarks`,
        {
          method: 'POST',
          body: JSON.stringify({
            name: benchName.trim(),
            description: benchDesc.trim(),
            prompts: benchPromptsText
              .split('\n')
              .map((line) => line.trim())
              .filter(Boolean),
          }),
        },
        accessToken,
      ),
    onSuccess: () => {
      setBenchError(null);
      setBenchName('');
      setBenchDesc('');
      setBenchPromptsText('');
      setBenchStatus(t('settings.benchmarkSuccess'));
      void queryClient.invalidateQueries({ queryKey: ['benchmarks', activeWorkspaceId] });
    },
    onError: (err) => {
      setBenchStatus(null);
      setBenchError(err instanceof ApiError ? err.message : t('settings.benchmarkError'));
    },
  });

  const deleteBenchmarkMut = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(
        `/workspaces/${activeWorkspaceId}/benchmarks/${id}`,
        { method: 'DELETE' },
        accessToken,
      ),
    onSuccess: () => {
      setBenchError(null);
      void queryClient.invalidateQueries({ queryKey: ['benchmarks', activeWorkspaceId] });
    },
    onError: (err) => {
      setBenchStatus(null);
      setBenchError(err instanceof ApiError ? err.message : t('settings.benchmarkError'));
    },
  });

  const updateRetentionMut = useMutation({
    mutationFn: () =>
      apiFetch<RetentionPolicyItem>(
        `/workspaces/${activeWorkspaceId}/retention`,
        {
          method: 'PUT',
          body: JSON.stringify({
            default_retention_days: retentionDaysInput.trim()
              ? Number(retentionDaysInput.trim())
              : null,
          }),
        },
        accessToken,
      ),
    onSuccess: () => {
      setRetentionError(null);
      setRetentionStatus(t('settings.retentionSaved'));
      void queryClient.invalidateQueries({ queryKey: ['retention', activeWorkspaceId] });
    },
    onError: (err) => {
      setRetentionStatus(null);
      setRetentionError(err instanceof ApiError ? err.message : t('settings.retentionError'));
    },
  });

  const purgeRetentionMut = useMutation({
    mutationFn: () =>
      apiFetch<RetentionPurgeItem>(
        `/workspaces/${activeWorkspaceId}/retention/purge`,
        { method: 'POST' },
        accessToken,
      ),
    onSuccess: (res) => {
      setRetentionError(null);
      setRetentionStatus(
        t('settings.retentionPurgeResult', {
          datasets: res.purged_datasets,
          versions: res.purged_versions,
        }),
      );
      void queryClient.invalidateQueries({ queryKey: ['datasets', activeWorkspaceId] });
    },
    onError: (err) => {
      setRetentionStatus(null);
      setRetentionError(err instanceof ApiError ? err.message : t('settings.retentionError'));
    },
  });

  async function handleBenchmarkFileUpload(file: File) {
    setBenchError(null);
    setBenchStatus(null);
    const baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1';
    const form = new FormData();
    form.append('name', benchName.trim() || file.name);
    form.append('description', benchDesc.trim());
    form.append('file', file);
    try {
      const res = await fetch(`${baseUrl}/workspaces/${activeWorkspaceId}/benchmarks/upload`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${accessToken}` },
        body: form,
      });
      if (!res.ok) {
        const errBody = (await res.json().catch(() => null)) as {
          error?: { message?: string };
        } | null;
        throw new Error(errBody?.error?.message || t('settings.benchmarkError'));
      }
      setBenchName('');
      setBenchDesc('');
      setBenchStatus(t('settings.benchmarkSuccess'));
      void queryClient.invalidateQueries({ queryKey: ['benchmarks', activeWorkspaceId] });
    } catch (err) {
      setBenchError(err instanceof Error && err.message ? err.message : t('settings.benchmarkError'));
    }
  }

  function toggleListItem(current: string[], val: string, setter: (v: string[]) => void) {
    if (current.includes(val)) {
      if (current.length > 1) setter(current.filter((x) => x !== val));
    } else {
      setter([...current, val]);
    }
  }

  function handleCreateToken(e: FormEvent) {
    e.preventDefault();
    if (!tokenName.trim()) return;
    createTokenMut.mutate();
  }

  function handleCreateWebhook(e: FormEvent) {
    e.preventDefault();
    if (!hookName.trim() || !hookUrl.trim()) return;
    createWebhookMut.mutate();
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-text-primary">{t('settings.title')}</h1>
        <p className="mt-1 text-sm text-text-secondary">{t('settings.subtitle')}</p>
      </div>

      <div className="flex flex-wrap gap-2 border-b border-border pb-3" role="tablist">
        <Button
          role="tab"
          aria-selected={tab === 'tokens'}
          variant={tab === 'tokens' ? 'primary' : 'secondary'}
          onClick={() => setTab('tokens')}
        >
          {t('settings.tabTokens')}
        </Button>
        <Button
          role="tab"
          aria-selected={tab === 'webhooks'}
          variant={tab === 'webhooks' ? 'primary' : 'secondary'}
          onClick={() => setTab('webhooks')}
        >
          {t('settings.tabWebhooks')}
        </Button>
        <Button
          role="tab"
          aria-selected={tab === 'sso'}
          variant={tab === 'sso' ? 'primary' : 'secondary'}
          onClick={() => setTab('sso')}
        >
          {t('settings.tabSso')}
        </Button>
        <Button
          role="tab"
          aria-selected={tab === 'benchmarks'}
          variant={tab === 'benchmarks' ? 'primary' : 'secondary'}
          onClick={() => setTab('benchmarks')}
        >
          {t('settings.tabBenchmarks')}
        </Button>
        <Button
          role="tab"
          aria-selected={tab === 'retention'}
          variant={tab === 'retention' ? 'primary' : 'secondary'}
          onClick={() => setTab('retention')}
        >
          {t('settings.tabRetention')}
        </Button>
      </div>

      {tab === 'tokens' && (
        <div className="space-y-4">
          <Card className="p-4">
            <form onSubmit={handleCreateToken} className="space-y-3">
              <div>
                <label
                  htmlFor="token-name"
                  className="mb-1 block text-xs font-medium text-text-primary"
                >
                  {t('settings.tokenNameLabel')}
                </label>
                <Input
                  id="token-name"
                  placeholder={t('settings.tokenNamePlaceholder')}
                  value={tokenName}
                  onChange={(e) => setTokenName(e.target.value)}
                />
              </div>

              <div>
                <span className="mb-1.5 block text-xs font-medium text-text-primary">
                  {t('settings.tokenScopesLabel')}
                </span>
                <div className="flex flex-wrap gap-3 text-xs">
                  {ALL_SCOPES.map((sc) => (
                    <label key={sc} className="flex items-center gap-1.5 text-text-secondary">
                      <input
                        type="checkbox"
                        checked={tokenScopes.includes(sc)}
                        onChange={() => toggleListItem(tokenScopes, sc, setTokenScopes)}
                      />
                      <span className="font-mono">{sc}</span>
                    </label>
                  ))}
                </div>
              </div>

              <Button type="submit" disabled={createTokenMut.isPending}>
                {t('settings.tokenCreateBtn')}
              </Button>
            </form>

            {revealedToken && (
              <div className="mt-4 rounded-control border border-border bg-subtle p-3 text-xs">
                <p className="font-medium text-warning-600">{t('settings.tokenSecretWarning')}</p>
                <code className="mt-1 block break-all font-mono text-text-primary">
                  {revealedToken}
                </code>
              </div>
            )}

            {tokenError && (
              <p role="alert" className="mt-3 text-xs text-danger-600">
                {tokenError}
              </p>
            )}
          </Card>

          {tokensQuery.isLoading && <Skeleton className="h-32 w-full" />}
          {tokensQuery.error && (
            <ErrorState
              message={
                tokensQuery.error instanceof ApiError
                  ? tokensQuery.error.message
                  : t('settings.tokenError')
              }
              onRetry={() => tokensQuery.refetch()}
            />
          )}
          {tokensQuery.data && tokensQuery.data.length === 0 && (
            <Card className="p-4 text-xs text-text-muted">{t('settings.tokenEmpty')}</Card>
          )}
          {tokensQuery.data && tokensQuery.data.length > 0 && (
            <Card className="divide-y divide-border p-0">
              {tokensQuery.data.map((tok) => (
                <div key={tok.id} className="flex flex-wrap items-center justify-between gap-3 p-4 text-xs">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2 font-medium text-text-primary">
                      <span>{tok.name}</span>
                      {tok.is_revoked && (
                        <Badge tone="danger">{t('settings.tokenRevokedBadge')}</Badge>
                      )}
                    </div>
                    <div className="font-mono text-text-muted">
                      {tok.scopes.join(', ')} · {formatDate(tok.created_at, locale)}
                    </div>
                  </div>
                  {!tok.is_revoked && (
                    <Button
                      variant="destructive"
                      className="px-2.5 py-1 text-xs"
                      onClick={() => revokeTokenMut.mutate(tok.id)}
                    >
                      {t('settings.tokenRevokeBtn')}
                    </Button>
                  )}
                </div>
              ))}
            </Card>
          )}
        </div>
      )}

      {tab === 'webhooks' && (
        <div className="space-y-4">
          <Card className="p-4">
            <form onSubmit={handleCreateWebhook} className="space-y-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="webhook-name"
                    className="mb-1 block text-xs font-medium text-text-primary"
                  >
                    {t('settings.webhookNameLabel')}
                  </label>
                  <Input
                    id="webhook-name"
                    placeholder={t('settings.webhookNamePlaceholder')}
                    value={hookName}
                    onChange={(e) => setHookName(e.target.value)}
                  />
                </div>
                <div>
                  <label
                    htmlFor="webhook-url"
                    className="mb-1 block text-xs font-medium text-text-primary"
                  >
                    {t('settings.webhookUrlLabel')}
                  </label>
                  <Input
                    id="webhook-url"
                    type="url"
                    placeholder="https://hooks.example.com/qualitext"
                    value={hookUrl}
                    onChange={(e) => setHookUrl(e.target.value)}
                  />
                </div>
              </div>

              <div>
                <span className="mb-1.5 block text-xs font-medium text-text-primary">
                  {t('settings.webhookEventsLabel')}
                </span>
                <div className="flex flex-wrap gap-3 text-xs">
                  {ALL_WEBHOOK_EVENTS.map((ev) => (
                    <label key={ev} className="flex items-center gap-1.5 text-text-secondary">
                      <input
                        type="checkbox"
                        checked={hookEvents.includes(ev)}
                        onChange={() => toggleListItem(hookEvents, ev, setHookEvents)}
                      />
                      <span className="font-mono">{ev}</span>
                    </label>
                  ))}
                </div>
              </div>

              <Button type="submit" disabled={createWebhookMut.isPending}>
                {t('settings.webhookCreateBtn')}
              </Button>
            </form>

            {revealedSecret && (
              <div className="mt-4 rounded-control border border-border bg-subtle p-3 text-xs">
                <p className="font-medium text-warning-600">{t('settings.webhookSecretWarning')}</p>
                <code className="mt-1 block break-all font-mono text-text-primary">
                  {revealedSecret}
                </code>
              </div>
            )}

            {webhookStatus && <p className="mt-3 text-xs text-success-600">{webhookStatus}</p>}
            {webhookError && (
              <p role="alert" className="mt-3 text-xs text-danger-600">
                {webhookError}
              </p>
            )}
          </Card>

          {webhooksQuery.isLoading && <Skeleton className="h-32 w-full" />}
          {webhooksQuery.error && (
            <ErrorState
              message={
                webhooksQuery.error instanceof ApiError
                  ? webhooksQuery.error.message
                  : t('settings.webhookError')
              }
              onRetry={() => webhooksQuery.refetch()}
            />
          )}
          {webhooksQuery.data && webhooksQuery.data.length === 0 && (
            <Card className="p-4 text-xs text-text-muted">{t('settings.webhookEmpty')}</Card>
          )}
          {webhooksQuery.data && webhooksQuery.data.length > 0 && (
            <Card className="divide-y divide-border p-0">
              {webhooksQuery.data.map((wh) => (
                <div key={wh.id} className="flex flex-wrap items-center justify-between gap-3 p-4 text-xs">
                  <div className="space-y-1">
                    <div className="font-medium text-text-primary">{wh.name}</div>
                    <div className="font-mono text-text-muted">{wh.url}</div>
                    <div className="text-text-secondary">{wh.events.join(', ')}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="secondary"
                      className="px-2.5 py-1 text-xs"
                      onClick={() => testWebhookMut.mutate(wh.id)}
                    >
                      {t('settings.webhookTestBtn')}
                    </Button>
                    <Button
                      variant="destructive"
                      className="px-2.5 py-1 text-xs"
                      onClick={() => deleteWebhookMut.mutate(wh.id)}
                    >
                      {t('settings.webhookDeleteBtn')}
                    </Button>
                  </div>
                </div>
              ))}
            </Card>
          )}
        </div>
      )}

      {tab === 'sso' && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Card className="space-y-2 p-4 text-xs">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-text-primary">
                {t('settings.ssoOidcTitle')}
              </h2>
              <Badge tone={ssoQuery.data?.oidc_enabled ? 'success' : 'neutral'}>
                {ssoQuery.data?.oidc_enabled ? t('settings.ssoEnabled') : t('settings.ssoDisabled')}
              </Badge>
            </div>
            {ssoQuery.data?.oidc_provider_name && (
              <p className="text-text-secondary">{ssoQuery.data.oidc_provider_name}</p>
            )}
          </Card>

          <Card className="space-y-2 p-4 text-xs">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-text-primary">
                {t('settings.ssoSamlTitle')}
              </h2>
              <Badge tone={ssoQuery.data?.saml_enabled ? 'success' : 'neutral'}>
                {ssoQuery.data?.saml_enabled ? t('settings.ssoEnabled') : t('settings.ssoDisabled')}
              </Badge>
            </div>
            <p className="text-text-secondary">{t('settings.ssoMetadataLabel')}:</p>
            <code className="block font-mono text-text-primary">/api/v1/auth/saml/metadata</code>
          </Card>
        </div>
      )}

      {tab === 'benchmarks' && (
        <div className="space-y-4">
          <Card className="p-4">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (!benchName.trim() || !benchPromptsText.trim()) return;
                createBenchmarkMut.mutate();
              }}
              className="space-y-3"
            >
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="bench-name"
                    className="mb-1 block text-xs font-medium text-text-primary"
                  >
                    {t('settings.benchmarkNameLabel')}
                  </label>
                  <Input
                    id="bench-name"
                    placeholder={t('settings.benchmarkNamePlaceholder')}
                    value={benchName}
                    onChange={(e) => setBenchName(e.target.value)}
                  />
                </div>
                <div>
                  <label
                    htmlFor="bench-desc"
                    className="mb-1 block text-xs font-medium text-text-primary"
                  >
                    {t('settings.benchmarkDescLabel')}
                  </label>
                  <Input
                    id="bench-desc"
                    value={benchDesc}
                    onChange={(e) => setBenchDesc(e.target.value)}
                  />
                </div>
              </div>

              <div>
                <label
                  htmlFor="bench-prompts"
                  className="mb-1 block text-xs font-medium text-text-primary"
                >
                  {t('settings.benchmarkPromptsLabel')}
                </label>
                <textarea
                  id="bench-prompts"
                  rows={4}
                  placeholder={t('settings.benchmarkPromptsPlaceholder')}
                  value={benchPromptsText}
                  onChange={(e) => setBenchPromptsText(e.target.value)}
                  className="w-full rounded-control border border-border bg-surface p-2.5 text-xs text-text-primary focus:outline-none focus:ring-2 focus:ring-brand-600"
                />
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit" disabled={createBenchmarkMut.isPending}>
                  {t('settings.benchmarkCreateBtn')}
                </Button>
                <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-control border border-border bg-surface px-3 py-2 text-xs font-medium text-text-primary hover:bg-subtle">
                  <span>{t('settings.benchmarkUploadBtn')}</span>
                  <input
                    type="file"
                    accept=".jsonl,.txt,.md"
                    className="sr-only"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      e.target.value = '';
                      if (file) void handleBenchmarkFileUpload(file);
                    }}
                  />
                </label>
              </div>
            </form>

            {benchStatus && <p className="mt-3 text-xs text-success-600">{benchStatus}</p>}
            {benchError && (
              <p role="alert" className="mt-3 text-xs text-danger-600">
                {benchError}
              </p>
            )}
          </Card>

          {benchmarksQuery.isLoading && <Skeleton className="h-32 w-full" />}
          {benchmarksQuery.error && (
            <ErrorState
              message={
                benchmarksQuery.error instanceof ApiError
                  ? benchmarksQuery.error.message
                  : t('settings.benchmarkError')
              }
              onRetry={() => benchmarksQuery.refetch()}
            />
          )}
          {benchmarksQuery.data && benchmarksQuery.data.length === 0 && (
            <Card className="p-4 text-xs text-text-muted">{t('settings.benchmarkEmpty')}</Card>
          )}
          {benchmarksQuery.data && benchmarksQuery.data.length > 0 && (
            <Card className="divide-y divide-border p-0">
              {benchmarksQuery.data.map((item) => (
                <div
                  key={item.id}
                  className="flex flex-wrap items-center justify-between gap-3 p-4 text-xs"
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-2 font-medium text-text-primary">
                      <span>{item.name}</span>
                      <Badge tone="info">
                        {t('settings.benchmarkEntryCount', { count: item.entry_count })}
                      </Badge>
                    </div>
                    {item.description && <p className="text-text-secondary">{item.description}</p>}
                    <div className="font-mono text-text-muted">
                      {formatDate(item.created_at, locale)}
                    </div>
                  </div>
                  <Button
                    variant="destructive"
                    className="px-2.5 py-1 text-xs"
                    onClick={() => deleteBenchmarkMut.mutate(item.id)}
                  >
                    {t('settings.benchmarkDeleteBtn')}
                  </Button>
                </div>
              ))}
            </Card>
          )}
        </div>
      )}

      {tab === 'retention' && (
        <div className="space-y-4">
          <Card className="space-y-4 p-4">
            <div>
              <h2 className="text-sm font-semibold text-text-primary">
                {t('settings.retentionTitle')}
              </h2>
              <p className="mt-1 text-xs text-text-secondary">{t('settings.retentionDesc')}</p>
            </div>

            {retentionQuery.isLoading && <Skeleton className="h-20 w-full" />}
            {retentionQuery.error && (
              <ErrorState
                message={
                  retentionQuery.error instanceof ApiError
                    ? retentionQuery.error.message
                    : t('settings.retentionError')
                }
                onRetry={() => retentionQuery.refetch()}
              />
            )}

            <form
              onSubmit={(e) => {
                e.preventDefault();
                updateRetentionMut.mutate();
              }}
              className="space-y-3"
            >
              <div className="max-w-xs">
                <label
                  htmlFor="retention-days"
                  className="mb-1 block text-xs font-medium text-text-primary"
                >
                  {t('settings.retentionDaysLabel')}
                </label>
                <Input
                  id="retention-days"
                  type="number"
                  min={1}
                  max={3650}
                  value={retentionDaysInput}
                  onChange={(e) => setRetentionDaysInput(e.target.value)}
                />
                <p className="mt-1 text-xs text-text-muted">{t('settings.retentionDaysHint')}</p>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit" disabled={updateRetentionMut.isPending}>
                  {t('settings.retentionSaveBtn')}
                </Button>
                <Button
                  type="button"
                  variant="destructive"
                  disabled={purgeRetentionMut.isPending}
                  onClick={() => purgeRetentionMut.mutate()}
                >
                  {t('settings.retentionPurgeBtn')}
                </Button>
              </div>
            </form>

            {retentionStatus && <p className="text-xs text-success-600">{retentionStatus}</p>}
            {retentionError && (
              <p role="alert" className="text-xs text-danger-600">
                {retentionError}
              </p>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
