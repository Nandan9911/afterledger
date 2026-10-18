import { defineRouting } from 'next-intl/routing';

export const locales = ['en', 'hi', 'ja', 'es', 'ml', 'ar'] as const;
export type Locale = (typeof locales)[number];

/** Each language's own name, as the language switcher shows it. */
export const localeNames: Record<Locale, string> = {
  en: 'English',
  hi: 'हिन्दी',
  ja: '日本語',
  es: 'Español',
  ml: 'മലയാളം',
  ar: 'العربية',
};

export const rtlLocales: readonly string[] = ['ar'];

export const routing = defineRouting({
  locales,
  defaultLocale: 'en',
  localePrefix: 'always',
});
