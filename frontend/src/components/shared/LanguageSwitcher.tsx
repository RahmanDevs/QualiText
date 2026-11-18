import { Languages } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useTranslation } from '@/lib/i18n';

export function LanguageSwitcher() {
  const { locale, setLocale, t } = useTranslation();
  const nextLocale = locale === 'id' ? 'en' : 'id';

  return (
    <Button
      variant="ghost"
      onClick={() => setLocale(nextLocale)}
      aria-label={t('app.language')}
      className="px-2.5 text-xs uppercase"
    >
      <Languages className="h-4 w-4" />
      <span>{locale}</span>
    </Button>
  );
}
