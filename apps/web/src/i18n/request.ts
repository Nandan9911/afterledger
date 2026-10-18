import { hasLocale } from 'next-intl';
import { getRequestConfig } from 'next-intl/server';
import { routing } from './routing';

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale = hasLocale(routing.locales, requested) ? requested : routing.defaultLocale;
  const base = (await import('../../messages/en.json')).default;
  const own = locale === 'en' ? base : (await import(`../../messages/${locale}.json`)).default;
  return { locale, messages: deepMerge(base, own), timeZone: 'UTC' };
});

/** Missing keys in a language fall back to English rather than showing a key. */
function deepMerge(base: Record<string, unknown>, own: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...base };
  for (const [k, v] of Object.entries(own)) {
    const b = base[k];
    out[k] =
      v && typeof v === 'object' && !Array.isArray(v) && b && typeof b === 'object'
        ? deepMerge(b as Record<string, unknown>, v as Record<string, unknown>)
        : v;
  }
  return out;
}
