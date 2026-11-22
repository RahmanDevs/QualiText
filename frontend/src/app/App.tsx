import { useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from '@/app/AppShell';
import { LoginPage } from '@/app/LoginPage';
import { OverviewPage } from '@/app/OverviewPage';
import { AuditLogsPage } from '@/features/audit/AuditLogsPage';
import { DatasetsPage } from '@/features/datasets/DatasetsPage';
import { DedupReviewPage } from '@/features/dedup/DedupReviewPage';
import { ExplorerPage } from '@/features/explorer/ExplorerPage';
import { PoliciesPage } from '@/features/policies/PoliciesPage';
import { RunsPage } from '@/features/runs/RunsPage';
import { SettingsPage } from '@/features/settings/SettingsPage';
import { useAuthStore } from '@/lib/auth';

const queryClient = new QueryClient();

export function App() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const userId = useAuthStore((s) => s.user?.id ?? null);

  useEffect(() => {
    if (!accessToken) {
      queryClient.clear();
    }
  }, [accessToken]);

  useEffect(() => {
    queryClient.clear();
  }, [userId]);

  if (!accessToken) {
    return (
      <QueryClientProvider client={queryClient}>
        <LoginPage />
      </QueryClientProvider>
    );
  }

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppShell>
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/datasets" element={<DatasetsPage />} />
            <Route path="/runs" element={<RunsPage />} />
            <Route path="/explorer" element={<ExplorerPage />} />
            <Route path="/dedup" element={<DedupReviewPage />} />
            <Route path="/policies" element={<PoliciesPage />} />
            <Route path="/reports" element={<RunsPage />} />
            <Route path="/audit-logs" element={<AuditLogsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AppShell>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
