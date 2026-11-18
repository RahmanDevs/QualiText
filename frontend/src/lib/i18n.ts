import { create } from 'zustand';
import idLocale from '@/locales/id.json';
import enLocale from '@/locales/en.json';

export type Locale = 'id' | 'en';

const LOCALES: Record<Locale, Record<string, unknown>> = {
  id: idLocale,
  en: enLocale,
};

interface I18nState {
  locale: Locale;
  setLocale: (locale: Locale) => void;
}

export const useI18nStore = create<I18nState>((set) => ({
  locale: 'id',
  setLocale: (locale) => set({ locale }),
}));

function lookupKey(obj: Record<string, unknown>, path: string): string {
  const parts = path.split('.');
  let current: unknown = obj;
  for (const part of parts) {
    if (typeof current === 'object' && current !== null && part in current) {
      current = (current as Record<string, unknown>)[part];
    } else {
      return path;
    }
  }
  return typeof current === 'string' ? current : path;
}

export function useTranslation() {
  const locale = useI18nStore((s) => s.locale);
  const setLocale = useI18nStore((s) => s.setLocale);
  const dict = LOCALES[locale];

  const t = (key: string): string => lookupKey(dict, key);
  return { t, locale, setLocale };
}

export function formatNumber(value: number, locale: Locale = 'id'): string {
  return new Intl.NumberFormat(locale === 'id' ? 'id-ID' : 'en-US').format(value);
}
