import { create } from 'zustand';
import idLocale from '@/locales/id.json';
import enLocale from '@/locales/en.json';

export type Locale = 'id' | 'en';

const LOCALES: Record<Locale, Record<string, unknown>> = {
  id: idLocale,
  en: enLocale,
};

const DEFAULT_LOCALE: Locale = import.meta.env.VITE_DEFAULT_LOCALE === 'en' ? 'en' : 'id';

interface I18nState {
  locale: Locale;
  setLocale: (locale: Locale) => void;
}

export const useI18nStore = create<I18nState>((set) => ({
  locale: DEFAULT_LOCALE,
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

  const t = (key: string, vars?: Record<string, string | number>): string => {
    let text = lookupKey(dict, key);
    if (vars) {
      for (const [k, v] of Object.entries(vars)) {
        text = text.replaceAll(`{${k}}`, String(v));
      }
    }
    return text;
  };
  return { t, locale, setLocale };
}

export function formatNumber(value: number, locale: Locale = 'id'): string {
  return new Intl.NumberFormat(locale === 'id' ? 'id-ID' : 'en-US').format(value);
}

export function formatDate(iso: string, locale: Locale = 'id'): string {
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return new Intl.DateTimeFormat(locale === 'id' ? 'id-ID' : 'en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(dt);
}
