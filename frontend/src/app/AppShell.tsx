import { useEffect, useState, type ReactNode } from 'react';
import {
  Activity,
  Database,
  FileBarChart2,
  GitCompare,
  LayoutDashboard,
  LogOut,
  Menu,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  TableProperties,
} from 'lucide-react';
import { NavLink, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
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
  { to: '/dedup', key: 'nav.dedup', icon: GitCompare },
  { to: '/policies', key: 'nav.policies', icon: SlidersHorizontal },
  { to: '/reports', key: 'nav.reports', icon: FileBarChart2 },
  { to: '/audit-logs', key: 'nav.auditLogs', icon: ShieldCheck },
  { to: '/settings', key: 'nav.settings', icon: Settings },
] as const;

export function AppShell({ children }: AppShellProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState('');
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user, activeWorkspaceId, setActiveWorkspace, logout } = useAuthStore();

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((prev) => !prev);
      } else if (e.key === 'Escape') {
        setPaletteOpen(false);
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const filteredCommands = NAV_ITEMS.filter((item) => {
    const label = t(item.key).toLowerCase();
    return label.includes(paletteQuery.trim().toLowerCase());
  });

  function handleSelectCommand(to: string) {
    setPaletteOpen(false);
    setPaletteQuery('');
    navigate(to);
  }

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

            <button
              type="button"
              onClick={() => setPaletteOpen(true)}
              className="hidden items-center gap-2 rounded-control border border-border bg-canvas px-3 py-1.5 text-xs text-text-muted transition-colors hover:border-brand-600 hover:text-text-primary md:flex"
            >
              <Search className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{t('app.searchPlaceholder')}</span>
            </button>
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

      {paletteOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={t('commandPalette.title')}
          className="fixed inset-0 z-50 flex items-start justify-center bg-canvas/80 p-4 pt-20"
        >
          <div className="w-full max-w-md space-y-3 rounded-card border border-border bg-surface p-4 shadow-sm">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-text-primary">
                {t('commandPalette.title')}
              </h2>
              <Button
                variant="ghost"
                className="px-2 py-1 text-xs"
                onClick={() => setPaletteOpen(false)}
              >
                {t('commandPalette.close')}
              </Button>
            </div>
            <Input
              autoFocus
              aria-label={t('commandPalette.placeholder')}
              placeholder={t('commandPalette.placeholder')}
              value={paletteQuery}
              onChange={(e) => setPaletteQuery(e.target.value)}
            />
            <ul className="max-h-60 divide-y divide-border overflow-y-auto text-xs">
              {filteredCommands.length === 0 ? (
                <li className="py-3 text-text-muted">{t('commandPalette.empty')}</li>
              ) : (
                filteredCommands.map((cmd) => (
                  <li key={cmd.to}>
                    <button
                      type="button"
                      onClick={() => handleSelectCommand(cmd.to)}
                      className="flex w-full items-center justify-between px-2 py-2.5 text-left text-text-primary hover:bg-subtle"
                    >
                      <span className="font-medium">{t(cmd.key)}</span>
                      <span className="font-mono text-text-muted">{cmd.to}</span>
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
