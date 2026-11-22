import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ExplorerPage } from '@/features/explorer/ExplorerPage';
import { RunsPage } from '@/features/runs/RunsPage';
import { useAuthStore } from '@/lib/auth';

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

describe('Stage 1 Hotfixes UI', () => {
  beforeEach(() => {
    useAuthStore.setState({
      accessToken: 'test-token',
      activeWorkspaceId: 'ws-1',
      user: {
        id: 'u-1',
        email: 'curator@qualitext.id',
        full_name: 'Raka Curator',
        is_superadmin: false,
        workspaces: [{ id: 'ws-1', name: 'Tim NLP', slug: 'tim-nlp', role: 'curator' }],
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useAuthStore.setState({ accessToken: null, user: null, activeWorkspaceId: null });
  });

  it('downloads HTML report using authenticated fetch + Blob URL (BUG-01)', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            run_id: 'run-1',
            version_id: 'ver-1',
            status: 'completed',
            dqi_total: 88,
            dqi_summary: { status_label: 'Baik', dimensions: [] },
            module_results: [],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response('<html><body>Laporan</body></html>', {
          status: 200,
          headers: { 'Content-Type': 'text/html' },
        }),
      );

    const createUrlSpy = vi.fn(() => 'blob:mock-report');
    const revokeUrlSpy = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createUrlSpy, configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeUrlSpy, configurable: true });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter initialEntries={['/runs?runId=run-1']}>
          <Routes>
            <Route path="/runs" element={<RunsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const dlButton = await screen.findByRole('button', { name: /Unduh Laporan HTML/i });
    fireEvent.click(dlButton);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(revokeUrlSpy).toHaveBeenCalledWith('blob:mock-report');
      expect(dlButton).not.toBeDisabled();
    });

    const [reportUrl, reportInit] = fetchMock.mock.calls[1];
    expect(String(reportUrl)).toMatch(/\/runs\/run-1\/report\.html$/);
    expect(reportInit?.headers).toEqual({ Authorization: 'Bearer test-token' });
    expect(createUrlSpy).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
  });

  it('edits prompt and response separately for structured SFT rows (BUG-03)', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            version_id: 'ver-1',
            total: 1,
            pii_revealed: false,
            items: [
              {
                row_id: 'row_000001',
                line_number: 1,
                prompt: 'Prompt lama',
                response: 'Response lama',
                text: 'Prompt lama\nResponse lama',
                is_valid: true,
                validation_error: '',
                curation_status: 'pending',
                has_pii: false,
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: 'ver-2', version_number: 2 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            version_id: 'ver-2',
            total: 1,
            pii_revealed: false,
            items: [],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      );

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter initialEntries={['/explorer?versionId=ver-1']}>
          <Routes>
            <Route path="/explorer" element={<ExplorerPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const editButton = await screen.findByRole('button', { name: /Perbaiki/i });
    fireEvent.click(editButton);

    const promptInput = screen.getByLabelText('Prompt');
    const responseInput = screen.getByLabelText('Response');
    expect(promptInput).toHaveValue('Prompt lama');
    expect(responseInput).toHaveValue('Response lama');

    fireEvent.change(promptInput, { target: { value: 'Prompt baru' } });
    fireEvent.change(responseInput, { target: { value: 'Response baru' } });

    fireEvent.click(screen.getByRole('button', { name: /Simpan ke Versi Baru/i }));

    await screen.findByText(/Perbaikan disimpan ke versi baru v2\./i);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/versions\/ver-1\/curation$/),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          actions: [
            {
              row_id: 'row_000001',
              action: 'edit',
              prompt: 'Prompt baru',
              response: 'Response baru',
            },
          ],
        }),
      }),
    );
  });
});
