import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from '@/app/AppShell';
import { LoginPage } from '@/app/LoginPage';
import { OverviewPage } from '@/app/OverviewPage';
import { PlaceholderPage } from '@/app/PlaceholderPage';
import { AuditLogsPage } from '@/features/audit/AuditLogsPage';
import { DatasetsPage } from '@/features/datasets/DatasetsPage';
import { ExplorerPage } from '@/features/explorer/ExplorerPage';
import { PoliciesPage } from '@/features/policies/PoliciesPage';
import { RunsPage } from '@/features/runs/RunsPage';
import { useAuthStore } from '@/lib/auth';

const queryClient = new QueryClient();

export function App() {
  const accessToken = useAuthStore((s) => s.accessToken);

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
            <Route path="/policies" element={<PoliciesPage />} />
            <Route path="/reports" element={<RunsPage />} />
            <Route path="/audit-logs" element={<AuditLogsPage />} />
            <Route path="/settings" element={<PlaceholderPage titleKey="nav.settings" />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AppShell>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
