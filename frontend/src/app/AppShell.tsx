import { useState, type ReactNode } from 'react';
import {
  Activity,
  Database,
  FileBarChart2,
  LayoutDashboard,
  LogOut,
  Menu,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  TableProperties,
} from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { Button } from '@/components/ui/Button';
import { LanguageSwitcher } from '@/components/shared/LanguageSwitcher';
import { ThemeToggle } from '@/components/shared/ThemeToggle';
import { useAuthStore } from '@/lib/auth';
import { useTranslation } from '@/lib/i18n';

interface AppShellProps {
  children: ReactNode;
}

const NAV_ITEMS = [
  { to: '/', key: 'nav.overview', icon: LayoutDashboard },
  { to: '/datasets', key: 'nav.datasets', icon: Database },
  { to: '/runs', key: 'nav.runs', icon: Activity },
  { to: '/explorer', key: 'nav.explorer', icon: TableProperties },
  { to: '/policies', key: 'nav.policies', icon: SlidersHorizontal },
  { to: '/reports', key: 'nav.reports', icon: FileBarChart2 },
  { to: '/audit-logs', key: 'nav.auditLogs', icon: ShieldCheck },
  { to: '/settings', key: 'nav.settings', icon: Settings },
] as const;

export function AppShell({ children }: AppShellProps) {
  const [collapsed, setCollapsed] = useState(false);
  const { t } = useTranslation();
  const { user, activeWorkspaceId, setActiveWorkspace, logout } = useAuthStore();

  return (
    <div className="flex min-h-screen bg-canvas text-text-primary">
      {/* Left Sidebar (248px / collapsed 64px per CLAUDE.md 3.2) */}
      <aside
        className={`flex flex-col border-r border-border bg-surface transition-all duration-150 ${
          collapsed ? 'w-16' : 'w-[248px]'
        }`}
        aria-label="Navigasi Utama"
      >
        <div className="flex h-14 items-center justify-between border-b border-border px-4">
          {!collapsed && <span className="text-base font-semibold tracking-tight">QualiText</span>}
          <Button
            variant="ghost"
            onClick={() => setCollapsed((c) => !c)}
            aria-label="Toggle sidebar"
            className="px-1.5"
          >
            <Menu className="h-4 w-4" />
          </Button>
        </div>

        <nav className="flex-1 space-y-1 p-2">
          {NAV_ITEMS.map(({ to, key, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-control px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-brand-50 text-brand-600'
                    : 'text-text-secondary hover:bg-subtle hover:text-text-primary'
                }`
              }
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              {!collapsed && <span className="truncate">{t(key)}</span>}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main Content Column */}
      <div className="flex flex-1 flex-col min-w-0">
        {/* Top Bar */}
        <header className="flex h-14 items-center justify-between border-b border-border bg-surface px-6">
          <div className="flex items-center gap-4">
            {user && user.workspaces.length > 0 && (
              <select
                aria-label="Workspace"
                value={activeWorkspaceId ?? user.workspaces[0].id}
                onChange={(e) => setActiveWorkspace(e.target.value)}
                className="rounded-control border border-border bg-surface px-2.5 py-1.5 text-sm text-text-primary focus:border-brand-600 focus:outline-none"
              >
                {user.workspaces.map((ws) => (
                  <option key={ws.id} value={ws.id}>
                    {ws.name} ({ws.role})
                  </option>
                ))}
              </select>
            )}

            <div className="hidden items-center gap-2 rounded-control border border-border bg-canvas px-3 py-1.5 text-xs text-text-muted md:flex">
              <Search className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{t('app.searchPlaceholder')}</span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <LanguageSwitcher />
            <ThemeToggle />
            {user && (
              <div className="ml-2 flex items-center gap-3 border-l border-border pl-3">
                <span className="hidden text-sm font-medium text-text-secondary sm:inline">
                  {user.full_name}
                </span>
                <Button variant="ghost" onClick={logout} aria-label={t('app.logout')} className="px-2">
                  <LogOut className="h-4 w-4" />
                </Button>
              </div>
            )}
          </div>
        </header>

        <main className="mx-auto w-full max-w-content flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
