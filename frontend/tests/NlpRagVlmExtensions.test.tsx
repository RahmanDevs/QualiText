import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DatasetsPage } from '@/features/datasets/DatasetsPage';
import { ExplorerPage } from '@/features/explorer/ExplorerPage';
import { RunsPage } from '@/features/runs/RunsPage';
import { useAuthStore } from '@/lib/auth';
import { useI18nStore } from '@/lib/i18n';

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

describe('NLP Linguistic, VLM PDF, Semantic Chunking & QA/SFT UI', () => {
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

  it('supports Pipeline NLP/RAG modal tabs for VLM verification, semantic chunking, and QA/SFT', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/workspaces/ws-1/datasets') && init?.method === 'GET') {
        return new Response(
          JSON.stringify([
            {
              id: 'ds-pdf-1',
              name: 'SOP PDF Korporat',
              description: '',
              dataset_type: 'rag',
              latest_version: {
                id: 'ver-pdf-1',
                version_number: 1,
                source_format: 'pdf',
                row_count: 3,
                invalid_row_count: 0,
                status: 'validated',
              },
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/versions/ver-pdf-1/vlm-verify') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({
            version_id: 'ver-pdf-1',
            total_pages: 1,
            verified_pages: 1,
            flagged_pages: 1,
            avg_vlm_accuracy_score: 0.72,
            avg_table_fidelity_score: 0.4,
            vlm_verification_score: 0.56,
            verification_mode: 'local_pdf_structural',
            render_backend: 'pypdf_inspector',
            pages: [
              {
                page_number: 1,
                accuracy_score: 0.72,
                table_fidelity_score: 0.4,
                has_embedded_images: false,
                has_table_grid: true,
                verification_mode: 'local_pdf_structural',
                issues: ['VLM_TABLE_STRUCTURE_MISMATCH'],
                extracted_preview: 'Kolom A Kolom B',
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/versions/ver-pdf-1/semantic-chunk') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({
            version: {
              id: 'ver-pdf-2',
              dataset_id: 'ds-pdf-1',
              version_number: 2,
              source_format: 'pdf',
              row_count: 4,
              invalid_row_count: 0,
              status: 'curated',
            },
            source_row_count: 3,
            chunk_count: 4,
            avg_tokens_per_chunk: 120.5,
            avg_coherence_score: 0.88,
            embedding_backend: 'local_hash',
            sample_chunks: [
              {
                row_id: 'row_000001',
                text_preview: 'Potongan kebijakan semantik pertama.',
                metadata: { language: 'id', token_count: 120 },
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.endsWith('/versions/ver-pdf-1/generate-qa') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({
            version: {
              id: 'ver-qa-1',
              dataset_id: 'ds-qa-1',
              version_number: 1,
              source_format: 'jsonl',
              row_count: 6,
              invalid_row_count: 0,
              status: 'validated',
            },
            source_rows_processed: 3,
            generated_pairs_count: 6,
            generation_mode: 'local_template',
            pii_masked: true,
            sample_pairs: [
              {
                row_id: 'row_000001',
                prompt: 'Apa isi kebijakan utama?',
                response: 'Melindungi data pelanggan.',
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
        <MemoryRouter initialEntries={['/datasets']}>
          <Routes>
            <Route path="/datasets" element={<DatasetsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Pipeline NLP\/RAG/i }));
    expect(
      await screen.findByRole('dialog', { name: /Pipeline Lanjutan NLP, VLM & RAG\/SFT/i }),
    ).toBeInTheDocument();

    // Tab 2: VLM PDF Verification
    fireEvent.click(screen.getByRole('tab', { name: /Verifikasi VLM PDF/i }));
    fireEvent.click(screen.getByRole('button', { name: /Verifikasi Ekstraksi PDF/i }));
    expect(await screen.findByText('VLM_TABLE_STRUCTURE_MISMATCH')).toBeInTheDocument();

    // Tab 3: Semantic Chunking & QA/SFT
    fireEvent.click(screen.getByRole('tab', { name: /Semantic Chunking & QA\/SFT/i }));
    fireEvent.click(screen.getByRole('button', { name: /Jalankan Semantic Chunking/i }));
    expect(await screen.findByText(/Terbentuk 4 chunk semantik/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Buat Pasangan QA\/SFT/i }));
    expect(await screen.findByText(/Berhasil menghasilkan 6 pasangan QA\/SFT/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/versions\/ver-pdf-1\/vlm-verify$/),
        expect.objectContaining({ method: 'POST' }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/versions\/ver-pdf-1\/semantic-chunk$/),
        expect.objectContaining({ method: 'POST' }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/versions\/ver-pdf-1\/generate-qa$/),
        expect.objectContaining({ method: 'POST' }),
      );
    });

    // Switch to English locale and check tab labels
    useI18nStore.getState().setLocale('en');
    expect(await screen.findByRole('tab', { name: /VLM PDF Verification/i })).toBeInTheDocument();
  });

  it('renders Linguistic Quality and VLM PDF cards on RunsPage and new exports/metadata on ExplorerPage', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/runs/run-ext-1/summary')) {
        return new Response(
          JSON.stringify({
            run_id: 'run-ext-1',
            version_id: 'ver-ext-1',
            status: 'completed',
            dqi_total: 88.5,
            dqi_summary: {
              status_label: 'Baik',
              is_partial: true,
              critical_capped: false,
              dimensions: [],
            },
            module_results: [
              {
                module: 'linguistic_quality',
                dimension: 'cleanliness_privacy',
                module_version: '0.1.0',
                metrics: {
                  avg_perplexity: 12.4,
                  p95_perplexity: 18.2,
                  valid_word_ratio: 0.92,
                  oov_ratio: 0.08,
                  gibberish_rows: 0,
                },
                findings_count: 0,
              },
              {
                module: 'vlm_pdf_verifier',
                dimension: 'instruction_quality',
                module_version: '0.1.0',
                metrics: {
                  total_pages: 4,
                  verified_pages: 4,
                  avg_vlm_accuracy_score: 0.95,
                  avg_table_fidelity_score: 0.9,
                  flagged_pages: 0,
                },
                findings_count: 0,
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.includes('/versions/ver-ext-1/rows')) {
        return new Response(
          JSON.stringify({
            version_id: 'ver-ext-1',
            total: 1,
            limit: 50,
            offset: 0,
            pii_revealed: false,
            items: [
              {
                row_id: 'row_000001',
                line_number: 1,
                prompt: '',
                response: '',
                text: 'Dokumen kebijakan privasi internal.',
                is_valid: true,
                validation_error: '',
                curation_status: 'pending',
                has_pii: false,
                metadata: {
                  language: 'id',
                  token_count: 8,
                  keywords: ['kebijakan', 'privasi'],
                },
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
        <MemoryRouter initialEntries={['/runs?runId=run-ext-1']}>
          <Routes>
            <Route path="/runs" element={<RunsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(
      await screen.findByText(/Kualitas Linguistik — Perplexity & Kosakata/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Verifikasi VLM & Struktur PDF/i)).toBeInTheDocument();
    expect(screen.getAllByText('12.4').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('92%')).toBeInTheDocument();

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter initialEntries={['/explorer?versionId=ver-ext-1']}>
          <Routes>
            <Route path="/explorer" element={<ExplorerPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const formatSelect = await screen.findByLabelText(/Format Ekspor/i);
    expect(formatSelect).toContainHTML('<option value="rag_vector">RAG / Vector DB (.jsonl)</option>');
    expect(formatSelect).toContainHTML('<option value="sft_chat">SFT ChatML / TRL (.jsonl)</option>');

    fireEvent.click(await screen.findByText('row_000001'));
    expect(await screen.findByText(/Metadata Chunk \/ SFT/i)).toBeInTheDocument();
    expect(screen.getByText(/keywords: kebijakan, privasi/i)).toBeInTheDocument();
  });
});
