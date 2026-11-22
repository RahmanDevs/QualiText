import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DqiTrendChart } from '@/components/shared/DqiTrendChart';
import { HighlightedText } from '@/components/shared/HighlightedText';
import { VirtualizedDataTable } from '@/components/shared/VirtualizedDataTable';
import { PoliciesPage } from '@/features/policies/PoliciesPage';
import { SettingsPage } from '@/features/settings/SettingsPage';
import { apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';
import { useI18nStore } from '@/lib/i18n';

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

describe('Audit Logic Fixes UI', () => {
  beforeEach(() => {
    useI18nStore.getState().setLocale('id');
    useAuthStore.setState({
      accessToken: 'test-token',
      activeWorkspaceId: 'ws-1',
      user: {
        id: 'u-1',
        email: 'viewer@qualitext.id',
        full_name: 'Viewer Test',
        is_superadmin: false,
        workspaces: [{ id: 'ws-1', name: 'Tim NLP', slug: 'tim-nlp', role: 'viewer' }],
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useAuthStore.setState({ accessToken: null, user: null, activeWorkspaceId: null });
  });

  it('does not force application/json Content-Type when apiFetch sends FormData', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    const form = new FormData();
    form.append('file', new Blob(['abc']), 'test.txt');

    await apiFetch('/datasets/1/versions/upload-direct', { method: 'POST', body: form }, 'tok');

    const [, init] = fetchMock.mock.calls[0];
    const headers = init?.headers as Headers;
    expect(headers.has('Content-Type')).toBe(false);
  });

  it('highlights [KARTU] alongside [NIK] in HighlightedText', () => {
    const { container } = render(
      <HighlightedText text="NIK saya [NIK] dan kartu [KARTU] terdeteksi." />,
    );
    const marks = Array.from(container.querySelectorAll('mark')).map((el) => el.textContent);
    expect(marks).toEqual(['[NIK]', '[KARTU]']);
  });

  it('resets VirtualizedDataTable scroll and renders rows when list shrinks', () => {
    const manyRows = Array.from({ length: 100 }, (_, i) => ({ id: `row_${i}` }));
    const cols = [{ key: 'id', header: 'ID', render: (r: { id: string }) => r.id }];

    const { container, rerender } = render(
      <VirtualizedDataTable rows={manyRows} getRowId={(r) => r.id} columns={cols} maxHeight={200} />,
    );
    const scroller = container.querySelector('.overflow-auto') as HTMLDivElement;
    fireEvent.scroll(scroller, { target: { scrollTop: 3000 } });

    rerender(
      <VirtualizedDataTable
        rows={[{ id: 'filtered_1' }, { id: 'filtered_2' }]}
        getRowId={(r) => r.id}
        columns={cols}
        maxHeight={200}
      />,
    );
    expect(screen.getByText('filtered_1')).toBeInTheDocument();
    expect(screen.getByText('filtered_2')).toBeInTheDocument();
  });

  it('keeps DqiTrendChart header safe when points shrink below hovered activeIdx', () => {
    const fourPoints = [1, 2, 3, 4].map((v) => ({
      run_id: `r-${v}`,
      version_number: v,
      dqi_total: 80 + v,
    }));
    const { container, rerender } = render(<DqiTrendChart points={fourPoints} />);
    const circles = container.querySelectorAll('circle');
    fireEvent.mouseEnter(circles[3]);

    rerender(
      <DqiTrendChart points={[{ run_id: 'r-only', version_number: 1, dqi_total: 92 }]} />,
    );
    expect(screen.getByText(/v1/i)).toBeInTheDocument();
    expect(screen.getByText('92')).toBeInTheDocument();
  });

  it('preserves existing dimension_weights when saving PoliciesPage', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if (init?.method === 'GET') {
        return new Response(
          JSON.stringify({
            min_dqi: 60,
            block_if_critical_pii: true,
            is_active: true,
            critical_cap: 55,
            dimension_thresholds: { structure_validity: 70 },
            dimension_weights: { cleanliness_privacy: 0.4, structure_validity: 0.2 },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return new Response(JSON.stringify({ min_dqi: 65 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <PoliciesPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const minDqiInput = await screen.findByLabelText(/Ambang Batas Minimum DQI/i);
    fireEvent.change(minDqiInput, { target: { value: '65' } });
    fireEvent.click(screen.getByRole('button', { name: /Simpan Kebijakan/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/workspaces\/ws-1\/policy$/),
        expect.objectContaining({
          method: 'PUT',
          body: expect.stringContaining('"cleanliness_privacy":0.4'),
        }),
      );
    });
  });

  it('renders ErrorState when SettingsPage webhooks query returns 403', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).endsWith('/webhooks')) {
        return new Response(
          JSON.stringify({ error: { code: 'FORBIDDEN', message: 'Akses webhook ditolak.' } }),
          { status: 403, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <SettingsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('tab', { name: /Webhook/i }));
    expect(await screen.findByText('Akses webhook ditolak.')).toBeInTheDocument();
  });
});
