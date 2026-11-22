import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppShell } from '@/app/AppShell';
import { OverviewPage } from '@/app/OverviewPage';
import { AuditLogsPage } from '@/features/audit/AuditLogsPage';
import { DedupReviewPage } from '@/features/dedup/DedupReviewPage';
import { PoliciesPage } from '@/features/policies/PoliciesPage';
import { RunsPage } from '@/features/runs/RunsPage';
import { SettingsPage } from '@/features/settings/SettingsPage';
import { useAuthStore } from '@/lib/auth';
import { useI18nStore } from '@/lib/i18n';

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

describe('Stage 3 v1.0 P1 & Enterprise UI', () => {
  beforeEach(() => {
    useI18nStore.getState().setLocale('id');
    useAuthStore.setState({
      accessToken: 'test-token',
      refreshToken: 'test-refresh',
      activeWorkspaceId: 'ws-1',
      user: {
        id: 'u-1',
        email: 'admin@qualitext.id',
        full_name: 'Admin QualiText',
        is_superadmin: true,
        workspaces: [{ id: 'ws-1', name: 'Tim NLP', slug: 'tim-nlp', role: 'admin' }],
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useI18nStore.getState().setLocale('id');
    useAuthStore.setState({
      accessToken: null,
      refreshToken: null,
      user: null,
      activeWorkspaceId: null,
    });
  });

  it('renders live OverviewPage KPIs, DqiTrendChart, and attention list', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).endsWith('/workspaces/ws-1/overview')) {
        return new Response(
          JSON.stringify({
            workspace_id: 'ws-1',
            total_datasets: 3,
            avg_dqi: 84.5,
            open_critical_pii: 2,
            active_runs: 1,
            datasets_needing_attention: [
              {
                dataset_id: 'ds-1',
                dataset_name: 'SFT CS ID',
                version_id: 'ver-1',
                dqi_total: 60,
                critical_pii: 2,
                invalid_row_count: 0,
                reason: 'critical_pii',
              },
            ],
            dqi_trend: [
              {
                run_id: 'run-1',
                dataset_id: 'ds-1',
                dataset_name: 'SFT CS ID',
                version_number: 1,
                dqi_total: 60,
                completed_at: '2026-11-19T00:00:00Z',
              },
              {
                run_id: 'run-2',
                dataset_id: 'ds-1',
                dataset_name: 'SFT CS ID',
                version_number: 2,
                dqi_total: 84.5,
                completed_at: '2026-11-19T01:00:00Z',
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return new Response('{}', { status: 200 });
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <OverviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('SFT CS ID')).toBeInTheDocument();
    expect(screen.getByText('2 temuan PII kritis terbuka')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Tren Data Quality Index/i })).toBeInTheDocument();
  });

  it('downloads PDF report and loads 2D topic clusters on demand in RunsPage', async () => {
    const createUrlSpy = vi.fn(() => 'blob:mock-pdf');
    const revokeUrlSpy = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createUrlSpy, configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeUrlSpy, configurable: true });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/runs/run-1/summary')) {
        return new Response(
          JSON.stringify({
            run_id: 'run-1',
            version_id: 'ver-1',
            status: 'completed',
            dqi_total: 91,
            dqi_summary: {
              status_label: 'Siap Latih',
              dimensions: [
                {
                  dimension: 'structure_validity',
                  label: 'Validitas Struktur',
                  score: 95,
                  weight: 0.2,
                  contribution: 19,
                  findings_count: 0,
                },
              ],
            },
            module_results: [],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/runs/run-1/report.pdf')) {
        return new Response('%PDF-1.4', {
          status: 200,
          headers: { 'Content-Type': 'application/pdf' },
        });
      }
      if (url.endsWith('/versions/ver-1/topic-clusters')) {
        return new Response(
          JSON.stringify({
            version_id: 'ver-1',
            entropy: 0.92,
            clusters: [{ cluster_id: 0, count: 2, top_terms: ['nlp', 'dataset'] }],
            points: [
              { row_id: 'row_1', x: -0.4, y: 0.5, cluster_id: 0, text_preview: 'Sampel teks' },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return new Response('{}', { status: 200 });
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter initialEntries={['/runs?runId=run-1']}>
          <Routes>
            <Route path="/runs" element={<RunsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const pdfBtn = await screen.findByRole('button', { name: /Unduh Laporan PDF/i });
    fireEvent.click(pdfBtn);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/runs\/run-1\/report\.pdf$/),
        expect.anything(),
      );
      expect(revokeUrlSpy).toHaveBeenCalledWith('blob:mock-pdf');
    });

    const clusterBtn = screen.getByRole('button', { name: /Visualisasi Klaster Topik 2D/i });
    fireEvent.click(clusterBtn);
    expect(await screen.findByRole('img', { name: /Sebaran Klaster Topik 2D/i })).toBeInTheDocument();
  });

  it('supports threshold slider and duplicate rejection in DedupReviewPage', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/dedup-preview')) {
        return new Response(
          JSON.stringify({
            version_id: 'ver-1',
            threshold: 0.88,
            scanned_rows: 2,
            duplicate_groups_count: 1,
            duplicate_rows_count: 1,
            embedding_backend: 'local_hash',
            groups: [
              {
                group_id: 'grp_0001',
                canonical_row_id: 'row_000002',
                avg_similarity: 0.94,
                members: [
                  {
                    row_id: 'row_000002',
                    similarity: 1.0,
                    is_canonical: true,
                    char_len: 50,
                    text_preview: 'Versi panjang kanonik',
                  },
                  {
                    row_id: 'row_000001',
                    similarity: 0.94,
                    is_canonical: false,
                    char_len: 20,
                    text_preview: 'Versi pendek duplikat',
                  },
                ],
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/versions/ver-1/curation')) {
        return new Response(JSON.stringify({ id: 'ver-2', version_number: 2 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response('{}', { status: 200 });
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter initialEntries={['/dedup?versionId=ver-1']}>
          <Routes>
            <Route path="/dedup" element={<DedupReviewPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Versi panjang kanonik')).toBeInTheDocument();
    const slider = screen.getByLabelText(/Ambang Batas Kemiripan Kosinus/i);
    fireEvent.change(slider, { target: { value: '0.75' } });
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/threshold=0\.75/),
        expect.anything(),
      );
    });

    fireEvent.click(await screen.findByRole('button', { name: /Tolak Semua Duplikat/i }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/versions\/ver-1\/curation$/),
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            actions: [{ row_id: 'row_000001', action: 'reject' }],
          }),
        }),
      );
    });
  });

  it('manages API tokens, webhooks, and SSO tabs in SettingsPage', async () => {
    let tokens = [
      {
        id: 'tok-old',
        name: 'Old Token',
        scopes: ['dataset:read'],
        expires_at: null,
        is_revoked: false,
        created_at: '2026-11-19T00:00:00Z',
      },
    ];
    let webhooks: Array<{
      id: string;
      name: string;
      url: string;
      events: string[];
      is_active: boolean;
      created_at: string;
    }> = [];

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/api-tokens') && init?.method === 'GET') {
        return new Response(JSON.stringify(tokens), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.endsWith('/api-tokens') && init?.method === 'POST') {
        tokens = [
          {
            id: 'tok-new',
            name: 'Pipeline CI',
            scopes: ['dataset:read', 'export:create'],
            expires_at: null,
            is_revoked: false,
            created_at: '2026-11-19T01:00:00Z',
          },
          ...tokens,
        ];
        return new Response(
          JSON.stringify({
            id: 'tok-new',
            name: 'Pipeline CI',
            token: 'qt_supersecret_value_once',
            scopes: ['dataset:read', 'export:create'],
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.includes('/api-tokens/') && init?.method === 'DELETE') {
        return new Response(null, { status: 204 });
      }
      if (url.endsWith('/webhooks') && init?.method === 'GET') {
        return new Response(JSON.stringify(webhooks), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.endsWith('/webhooks') && init?.method === 'POST') {
        webhooks = [
          {
            id: 'wh-1',
            name: 'Slack Hook',
            url: 'https://hooks.example.com/test',
            events: ['run.completed', 'export.completed'],
            is_active: true,
            created_at: '2026-11-19T01:00:00Z',
          },
        ];
        return new Response(
          JSON.stringify({
            id: 'wh-1',
            name: 'Slack Hook',
            signing_secret: 'whsec_testsecret123',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/wh-1/test') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({ delivered: true, status_code: 200, error_message: null }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/auth/sso/providers')) {
        return new Response(
          JSON.stringify({
            oidc_enabled: true,
            oidc_provider_name: 'Corp Keycloak',
            saml_enabled: true,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return new Response('{}', { status: 200 });
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <SettingsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Old Token')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Nama Token API/i), {
      target: { value: 'Pipeline CI' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Buat Token API/i }));
    expect(await screen.findByText('qt_supersecret_value_once')).toBeInTheDocument();

    // Switch to Webhooks tab
    fireEvent.click(screen.getByRole('tab', { name: /Webhook/i }));
    fireEvent.change(await screen.findByLabelText(/Nama Webhook/i), {
      target: { value: 'Slack Hook' },
    });
    fireEvent.change(screen.getByLabelText(/URL Tujuan HTTPS/i), {
      target: { value: 'https://hooks.example.com/test' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Tambah Webhook/i }));
    expect(await screen.findByText('whsec_testsecret123')).toBeInTheDocument();

    const testBtn = await screen.findByRole('button', { name: /Uji Kirim/i });
    fireEvent.click(testBtn);
    expect(await screen.findByText(/Uji webhook berhasil \(HTTP 200\)/i)).toBeInTheDocument();

    // Switch to SSO tab
    fireEvent.click(screen.getByRole('tab', { name: /SSO Korporat/i }));
    expect(await screen.findByText('Corp Keycloak')).toBeInTheDocument();
  });

  it('saves per-dimension policy thresholds, exports audit CSV, and opens CommandPalette on Ctrl+K', async () => {
    const createUrlSpy = vi.fn(() => 'blob:mock-audit');
    const revokeUrlSpy = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createUrlSpy, configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeUrlSpy, configurable: true });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/policy') && init?.method === 'GET') {
        return new Response(
          JSON.stringify({
            min_dqi: 60,
            block_if_critical_pii: true,
            is_active: true,
            critical_cap: 55,
            dimension_thresholds: { structure_validity: 80 },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/policy') && init?.method === 'PUT') {
        return new Response(JSON.stringify({ min_dqi: 60 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.endsWith('/audit-logs')) {
        return new Response(
          JSON.stringify([
            {
              id: 'a-1',
              created_at: '2026-11-19T00:00:00Z',
              user_id: 'u-1',
              action: 'policy.update',
              target_type: 'quality_policy',
              target_id: 'p-1',
              ip_address: '127.0.0.1',
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.includes('/audit-logs/export')) {
        return new Response('id,action\na-1,policy.update\n', {
          status: 200,
          headers: { 'Content-Type': 'text/csv' },
        });
      }
      return new Response('{}', { status: 200 });
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter initialEntries={['/policies']}>
          <AppShell>
            <PoliciesPage />
            <AuditLogsPage />
          </AppShell>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const structInput = await screen.findByLabelText(/Validitas Struktur/i);
    expect(structInput).toHaveValue(80);
    fireEvent.change(structInput, { target: { value: '85' } });
    fireEvent.click(screen.getByRole('button', { name: /Simpan Kebijakan/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/workspaces\/ws-1\/policy$/),
        expect.objectContaining({
          method: 'PUT',
          body: expect.stringContaining('"structure_validity":85'),
        }),
      );
    });

    expect(await screen.findByText('127.0.0.1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Unduh CSV/i }));
    await waitFor(() => {
      expect(revokeUrlSpy).toHaveBeenCalledWith('blob:mock-audit');
    });

    // Ctrl+K opens CommandPalette dialog
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(screen.getByRole('dialog', { name: /Palet Perintah Cepat/i })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
