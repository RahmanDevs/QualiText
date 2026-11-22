import { useEffect, useState, type FormEvent } from 'react';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { LanguageSwitcher } from '@/components/shared/LanguageSwitcher';
import { ThemeToggle } from '@/components/shared/ThemeToggle';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';
import { useTranslation } from '@/lib/i18n';

interface SsoProviders {
  oidc_enabled: boolean;
  oidc_provider_name: string;
  saml_enabled: boolean;
}

export function LoginPage() {
  const { t } = useTranslation();
  const login = useAuthStore((s) => s.login);
  const [email, setEmail] = useState(import.meta.env.DEV ? 'admin@qualitext.id' : '');
  const [password, setPassword] = useState(import.meta.env.DEV ? 'Admin123!' : '');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [ssoProviders, setSsoProviders] = useState<SsoProviders | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    apiFetch<SsoProviders>('/auth/sso/providers', {
      method: 'GET',
      signal: controller.signal,
    })
      .then((res) => setSsoProviders(res))
      .catch(() => {});
    return () => controller.abort();
  }, []);

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

  async function handleSsoRedirect(provider: 'oidc' | 'saml') {
    setError(null);
    try {
      if (provider === 'oidc') {
        const res = await apiFetch<{ authorization_url: string }>('/auth/oidc/login', {
          method: 'GET',
        });
        window.location.assign(res.authorization_url);
      } else {
        const res = await apiFetch<{ redirect_url: string }>('/auth/saml/login', {
          method: 'GET',
        });
        window.location.assign(res.redirect_url);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('auth.errorFallback'));
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

        {(ssoProviders?.oidc_enabled || ssoProviders?.saml_enabled) && (
          <div className="mt-4 space-y-2 border-t border-border pt-4">
            {ssoProviders.oidc_enabled && (
              <Button
                type="button"
                variant="secondary"
                className="w-full"
                onClick={() => void handleSsoRedirect('oidc')}
              >
                {t('auth.ssoOidcButton', { provider: ssoProviders.oidc_provider_name })}
              </Button>
            )}
            {ssoProviders.saml_enabled && (
              <Button
                type="button"
                variant="secondary"
                className="w-full"
                onClick={() => void handleSsoRedirect('saml')}
              >
                {t('auth.ssoSamlButton')}
              </Button>
            )}
          </div>
        )}

        {import.meta.env.DEV && <p className="mt-4 text-xs text-text-muted">{t('auth.demoHint')}</p>}
      </Card>
    </div>
  );
}
