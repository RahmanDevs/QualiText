import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import indexHtml from '../index.html?raw';
import { DatasetsPage } from '@/features/datasets/DatasetsPage';
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

describe('Stage 4 P2 Enterprise & Benchmarks UI', () => {
  beforeEach(() => {
    useI18nStore.getState().setLocale('id');
    useAuthStore.setState({
      accessToken: 'test-token',
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
    useAuthStore.setState({ accessToken: null, user: null, activeWorkspaceId: null });
  });

  it('supports Connector Import modal, chosen/rejected mapping, and Dataset Delete', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/workspaces/ws-1/datasets') && init?.method === 'GET') {
        return new Response(
          JSON.stringify([
            {
              id: 'ds-1',
              name: 'DPO Corpus ID',
              description: '',
              dataset_type: 'preference',
              retention_days: 60,
              latest_version: {
                id: 'ver-1',
                version_number: 1,
                source_format: 'jsonl',
                row_count: 5,
                invalid_row_count: 0,
                status: 'validated',
              },
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/versions/ver-1/mapping') && init?.method === 'GET') {
        return new Response(
          JSON.stringify({
            version_id: 'ver-1',
            mapping: {},
            detected_columns: ['good_ans'],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/datasets/ds-1/versions/import-connector') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({
            id: 'ver-2',
            version_number: 2,
            source_format: 'jsonl',
            row_count: 4,
            invalid_row_count: 0,
            status: 'validated',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/datasets/ds-1') && init?.method === 'DELETE') {
        return new Response(null, { status: 204 });
      }
      return new Response('{}', { status: 200 });
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter initialEntries={['/datasets']}>
          <Routes>
            <Route path="/datasets" element={<DatasetsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Retensi 60h')).toBeInTheDocument();

    // Open Column Mapping and verify chosen/rejected options exist
    fireEvent.click(screen.getByRole('button', { name: /Pemetaan Kolom/i }));
    const colSelect = await screen.findByLabelText('good_ans');
    expect(colSelect).toContainHTML('<option value="chosen">chosen</option>');
    expect(colSelect).toContainHTML('<option value="rejected">rejected</option>');

    // Open Connector Import dialog and submit SQL connector
    fireEvent.click(screen.getByRole('button', { name: /Impor Konektor/i }));
    const dialog = await screen.findByRole('dialog', { name: /Impor Versi via Konektor/i });
    expect(dialog).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Tipe Konektor/i), { target: { value: 'database' } });
    fireEvent.change(screen.getByLabelText(/URI Sumber/i), {
      target: { value: 'sqlite:///tmp/sample.db' },
    });
    fireEvent.change(screen.getByLabelText(/Nama Tabel SQL/i), { target: { value: 'sft_rows' } });
    fireEvent.click(screen.getByRole('button', { name: /Tarik & Impor Data/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/datasets\/ds-1\/versions\/import-connector$/),
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"connector_type":"database"'),
        }),
      );
    });

    // Two-step Dataset Delete
    fireEvent.click(screen.getByRole('button', { name: /^Hapus$/i }));
    fireEvent.click(await screen.findByRole('button', { name: /Konfirmasi Hapus/i }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/datasets\/ds-1$/),
        expect.objectContaining({ method: 'DELETE' }),
      );
    });
  });

  it('manages Benchmarks and UU PDP Retention tabs on SettingsPage', async () => {
    const benchmarks = [
      {
        id: 'b-1',
        workspace_id: 'ws-1',
        name: 'MMLU ID',
        description: 'Evaluasi dasar',
        entry_count: 10,
        is_active: true,
        created_at: '2026-11-19T00:00:00Z',
      },
    ];

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/benchmarks') && init?.method === 'GET') {
        return new Response(JSON.stringify(benchmarks), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.endsWith('/benchmarks') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({
            id: 'b-2',
            workspace_id: 'ws-1',
            name: 'IndoNLU',
            description: '',
            entry_count: 2,
            is_active: true,
            created_at: '2026-11-19T01:00:00Z',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/benchmarks/b-1') && init?.method === 'DELETE') {
        return new Response(null, { status: 204 });
      }
      if (url.endsWith('/retention') && init?.method === 'GET') {
        return new Response(JSON.stringify({ workspace_id: 'ws-1', default_retention_days: 90 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.endsWith('/retention') && init?.method === 'PUT') {
        return new Response(JSON.stringify({ workspace_id: 'ws-1', default_retention_days: 60 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.endsWith('/retention/purge') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({ workspace_id: 'ws-1', purged_datasets: 2, purged_versions: 3 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return new Response('[]', { status: 200 });
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <SettingsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // Switch to Benchmarks tab
    fireEvent.click(screen.getByRole('tab', { name: /Benchmark Kontaminasi/i }));
    expect(await screen.findByText('MMLU ID')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Nama Set Benchmark/i), {
      target: { value: 'IndoNLU' },
    });
    fireEvent.change(screen.getByLabelText(/Daftar Prompt Benchmark/i), {
      target: { value: 'Prompt satu\nPrompt dua' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Simpan Set Benchmark/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/workspaces\/ws-1\/benchmarks$/),
        expect.objectContaining({ method: 'POST' }),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: /^Hapus$/i }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/workspaces\/ws-1\/benchmarks\/b-1$/),
        expect.objectContaining({ method: 'DELETE' }),
      );
    });

    // Switch to UU PDP Retention tab
    fireEvent.click(screen.getByRole('tab', { name: /Retensi UU PDP/i }));
    const retInput = await screen.findByLabelText(/Retensi Default Workspace/i);
    await waitFor(() => expect(retInput).toHaveValue(90));

    fireEvent.change(retInput, { target: { value: '60' } });
    fireEvent.click(screen.getByRole('button', { name: /Simpan Kebijakan Retensi/i }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/workspaces\/ws-1\/retention$/),
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ default_retention_days: 60 }),
        }),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: /Jalankan Pembersihan Kedaluwarsa/i }));
    expect(await screen.findByText(/2 dataset \(3 versi\) kedaluwarsa/i)).toBeInTheDocument();

    // Verify English locale switch renders Stage 4 tabs cleanly
    useI18nStore.getState().setLocale('en');
    expect(await screen.findByRole('tab', { name: /Contamination Benchmarks/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /UU PDP Retention/i })).toBeInTheDocument();
  });

  it('ships air-gapped index.html with zero external Google Fonts URLs', () => {
    expect(indexHtml).not.toContain('fonts.googleapis.com');
    expect(indexHtml).not.toContain('fonts.gstatic.com');
  });
});
