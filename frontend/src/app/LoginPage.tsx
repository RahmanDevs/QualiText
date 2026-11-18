import { useState, type FormEvent } from 'react';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { LanguageSwitcher } from '@/components/shared/LanguageSwitcher';
import { ThemeToggle } from '@/components/shared/ThemeToggle';
import { ApiError } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';
import { useTranslation } from '@/lib/i18n';

export function LoginPage() {
  const { t } = useTranslation();
  const login = useAuthStore((s) => s.login);
  const [email, setEmail] = useState('admin@qualitext.id');
  const [password, setPassword] = useState('Admin123!');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('auth.errorFallback'));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-canvas p-4">
      <div className="absolute right-4 top-4 flex items-center gap-2">
        <LanguageSwitcher />
        <ThemeToggle />
      </div>

      <Card className="w-full max-w-md">
        <div className="mb-6">
          <h1 className="text-xl font-semibold text-text-primary">{t('auth.title')}</h1>
          <p className="mt-1 text-sm text-text-secondary">{t('auth.subtitle')}</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="email" className="mb-1 block text-sm font-medium text-text-primary">
              {t('auth.email')}
            </label>
            <Input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>

          <div>
            <label htmlFor="password" className="mb-1 block text-sm font-medium text-text-primary">
              {t('auth.password')}
            </label>
            <Input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          {error && (
            <div
              role="alert"
              className="rounded-control border border-border bg-subtle p-3 text-sm text-danger-600"
            >
              {error}
            </div>
          )}

          <Button type="submit" disabled={loading} className="w-full">
            {loading ? t('auth.submitting') : t('auth.submit')}
          </Button>
        </form>

        <p className="mt-4 text-xs text-text-muted">{t('auth.demoHint')}</p>
      </Card>
    </div>
  );
}
