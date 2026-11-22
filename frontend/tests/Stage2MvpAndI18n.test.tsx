import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScoreCard } from '@/components/shared/ScoreCard';
import { AuditLogsPage } from '@/features/audit/AuditLogsPage';
import { DatasetsPage } from '@/features/datasets/DatasetsPage';
import { ExplorerPage } from '@/features/explorer/ExplorerPage';
import { PoliciesPage } from '@/features/policies/PoliciesPage';
import { useAuthStore } from '@/lib/auth';
import { useI18nStore } from '@/lib/i18n';

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

describe('Stage 2 MVP P0 & i18n UI', () => {
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

  it('supports Column Mapping and Validation Errors download on DatasetsPage', async () => {
    const createUrlSpy = vi.fn(() => 'blob:mock-errors');
    const revokeUrlSpy = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createUrlSpy, configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeUrlSpy, configurable: true });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/workspaces/ws-1/datasets')) {
        return new Response(
          JSON.stringify([
            {
              id: 'ds-1',
              name: 'SFT Indo',
              description: '',
              dataset_type: 'sft',
              latest_version: {
                id: 'ver-1',
                version_number: 1,
                source_format: 'jsonl',
                row_count: 10,
                invalid_row_count: 1,
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
            detected_columns: ['instruction', 'output'],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/versions/ver-1/mapping') && init?.method === 'PUT') {
        return new Response(
          JSON.stringify({
            id: 'ver-1',
            version_number: 1,
            source_format: 'jsonl',
            row_count: 10,
            invalid_row_count: 0,
            status: 'validated',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/versions/ver-1/validation-errors')) {
        return new Response(
          JSON.stringify({
            version_id: 'ver-1',
            invalid_row_count: 1,
            errors: [{ line_number: 4, error_type: 'INVALID_JSON', message: 'Baris rusak' }],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
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

    const mapBtn = await screen.findByRole('button', { name: /Pemetaan Kolom/i });
    fireEvent.click(mapBtn);

    const instructionSelect = await screen.findByLabelText('instruction');
    fireEvent.change(instructionSelect, { target: { value: 'prompt' } });
    fireEvent.click(screen.getByRole('button', { name: /Simpan Pemetaan/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/versions\/ver-1\/mapping$/),
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ mapping: { instruction: 'prompt' } }),
        }),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: /Lihat Error/i }));
    await screen.findByText('INVALID_JSON');

    fireEvent.click(screen.getByRole('button', { name: /Unduh Error CSV/i }));
    expect(createUrlSpy).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeUrlSpy).toHaveBeenCalledWith('blob:mock-errors');
  });

  it('supports pagination, density, diff preview, huggingface export, and shortcuts on ExplorerPage', async () => {
    const createUrlSpy = vi.fn(() => 'blob:mock-hf');
    const revokeUrlSpy = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createUrlSpy, configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeUrlSpy, configurable: true });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/versions/ver-1/rows')) {
        return new Response(
          JSON.stringify({
            version_id: 'ver-1',
            total: 100,
            limit: 50,
            offset: 0,
            pii_revealed: false,
            items: [
              {
                row_id: 'row_000001',
                line_number: 1,
                prompt: 'NIK saya [NIK]',
                response: 'Dicatat',
                text: 'NIK saya [NIK]\nDicatat',
                is_valid: true,
                validation_error: '',
                curation_status: 'pending',
                has_pii: true,
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/curation/diff-preview')) {
        return new Response(
          JSON.stringify({
            before: '<p>NIK [NIK]</p>',
            after: 'NIK [NIK]',
            changed: true,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/versions/ver-1/exports')) {
        return new Response(
          JSON.stringify({ id: 'exp-hf-1', export_format: 'huggingface' }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/exports/exp-hf-1/download')) {
        return new Response('zipdata', {
          status: 200,
          headers: { 'Content-Type': 'application/zip' },
        });
      }
      return new Response('{}', { status: 200 });
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter initialEntries={['/explorer?versionId=ver-1']}>
          <Routes>
            <Route path="/explorer" element={<ExplorerPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const rowCell = await screen.findByText('row_000001');
    fireEvent.click(rowCell);

    // Detail panel + HighlightedText + DiffPreview
    const diffBtn = await screen.findByRole('button', { name: /Pratinjau Diff/i });
    fireEvent.click(diffBtn);
    await screen.findByText('<p>NIK [NIK]</p>');

    // Density toggle
    const densityBtn = screen.getByRole('button', { name: /Nyaman \(40px\)/i });
    fireEvent.click(densityBtn);
    expect(screen.getByRole('button', { name: /Padat \(32px\)/i })).toBeInTheDocument();

    // Checkbox reveals bulk curation actions including Flag, Hash PII, Drop Row
    const checkbox = screen.getByLabelText(/Pilih row_000001/i);
    fireEvent.click(checkbox);
    expect(screen.getByRole('button', { name: /Tandai/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Hash PII/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Hapus Baris/i })).toBeInTheDocument();

    // Pagination Next triggers offset=50
    fireEvent.click(screen.getByRole('button', { name: /Berikutnya/i }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/offset=50/),
        expect.anything(),
      );
    });

    // Export Hugging Face
    const formatSelect = screen.getByLabelText(/Format Ekspor/i);
    fireEvent.change(formatSelect, { target: { value: 'huggingface' } });
    fireEvent.click(screen.getByRole('button', { name: /^Ekspor$/i }));
    await waitFor(() => {
      expect(revokeUrlSpy).toHaveBeenCalledWith('blob:mock-hf');
    });

    // Keyboard shortcut '?' opens help dialog and Escape closes it
    fireEvent.keyDown(window, { key: '?' });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders English translations across PoliciesPage, AuditLogsPage, and ScoreCard', async () => {
    useI18nStore.getState().setLocale('en');

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/policy')) {
        return new Response(
          JSON.stringify({ min_dqi: 70, block_if_critical_pii: true, is_active: true }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/audit-logs')) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response('{}', { status: 200 });
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <PoliciesPage />
          <AuditLogsPage />
          <ScoreCard dqiTotal={80} statusLabel="Baik" isPartial={true} criticalCapped={false} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Quality Policy (Quality Gate)')).toBeInTheDocument();
    expect(await screen.findByText('No audit logs yet')).toBeInTheDocument();
    expect(screen.getByText('Partial Score')).toBeInTheDocument();
  });
});
