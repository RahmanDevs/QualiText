import { Moon, Sun } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useTranslation } from '@/lib/i18n';
import { useThemeStore } from '@/lib/theme';

export function ThemeToggle() {
  const { theme, toggleTheme } = useThemeStore();
  const { t } = useTranslation();
  const label = theme === 'light' ? t('app.themeDark') : t('app.themeLight');

  return (
    <Button
      variant="ghost"
      onClick={toggleTheme}
      aria-label={label}
      title={label}
      className="px-2.5"
    >
      {theme === 'light' ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
    </Button>
  );
}
