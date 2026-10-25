/** Dates and small words, in the reader's language. Amounts come from the API already formatted. */

const tz = 'UTC';

/** The regional form each UI language formats dates in: British English, Indian Hindi and Malayalam,
 * Japanese, US Spanish, Egyptian Arabic (with Arabic-Indic digits). */
const INTL: Record<string, string> = { en: 'en-GB', hi: 'hi-IN', ja: 'ja-JP', es: 'es-US', ml: 'ml-IN', ar: 'ar-EG' };
export const intl = (locale: string) => INTL[locale] ?? locale;

/** A date or date-time from the API. A time is shown on the clock it was written on (a demo family's story on
 * the family's own clock, new activity on the service's): its offset is set aside, the time as written is read as
 * UTC, and the formatters below format in UTC, so 09:00 in Lucknow stays 09:00 in any browser. */
export function asDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const written = iso.length === 10 ? `${iso}T00:00:00` : iso.replace(/([zZ]|[+-]\d{2}:?\d{2})$/, '');
  const d = new Date(`${written}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Whole days from one ISO date to another (dates without a time, so no time zone can shift them). */
export function daysBetween(from: string | null | undefined, to: string | null | undefined): number {
  const a = asDate(from);
  const b = asDate(to);
  return a && b ? Math.round((b.getTime() - a.getTime()) / 86_400_000) : 0;
}

export function dayMonth(iso: string | null | undefined, locale: string): string {
  const d = asDate(iso);
  return d ? new Intl.DateTimeFormat(intl(locale), { day: 'numeric', month: 'short', timeZone: tz }).format(d) : '';
}

export function longDate(iso: string | null | undefined, locale: string): string {
  const d = asDate(iso);
  return d ? new Intl.DateTimeFormat(intl(locale), { day: 'numeric', month: 'long', year: 'numeric', timeZone: tz }).format(d) : '';
}

export function weekdayDate(iso: string | null | undefined, locale: string): string {
  const d = asDate(iso);
  return d ? new Intl.DateTimeFormat(intl(locale), { weekday: 'long', day: 'numeric', month: 'long', timeZone: tz }).format(d) : '';
}

export function clockTime(iso: string | null | undefined, locale: string): string {
  const d = asDate(iso);
  return d ? new Intl.DateTimeFormat(intl(locale), { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: tz }).format(d) : '';
}

/** A Japanese era date with the Western date beside it: 令和8年9月7日（2026年9月7日）. */
export function eraDate(iso: string | null | undefined): string {
  const d = asDate(iso);
  if (!d) return '';
  const era = new Intl.DateTimeFormat('ja-JP-u-ca-japanese', {
    era: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    timeZone: tz,
  }).format(d);
  const west = new Intl.DateTimeFormat('ja-JP', { year: 'numeric', month: 'long', day: 'numeric', timeZone: tz }).format(d);
  return `${era}（${west}）`;
}

export function countryName(code: string | null | undefined, locale: string): string {
  if (!code) return '';
  try {
    return new Intl.DisplayNames([intl(locale)], { type: 'region' }).of(code.split('-')[0]) ?? code;
  } catch {
    return code;
  }
}

export function languageName(code: string | null | undefined, locale: string): string {
  if (!code) return '';
  try {
    return new Intl.DisplayNames([intl(locale)], { type: 'language' }).of(code) ?? code;
  } catch {
    return code;
  }
}

const KEEP_SECOND = new Set(['devi', 'amma', 'bai', 'begum', 'kaur', 'ben', 'bibi']);

/** The name a family uses in a list: the first name, or both words of names like "Kamla Devi", or the whole name
 * when it starts with an initial ("R. P. Shukla", not "R."). */
export function shortName(name: string | null | undefined): string {
  if (!name) return '';
  const parts = name.trim().split(/\s+/);
  if (parts.length === 2 && KEEP_SECOND.has(parts[1].toLowerCase())) return name.trim();
  if (/^\p{L}\.?$/u.test(parts[0])) return name.trim();
  return parts[0];
}

/** A case's title once the person who died is named; empty while the API still calls it "New case". */
export function caseTitle(title: string | null | undefined): string {
  return title && title !== 'New case' ? title : '';
}

/** Prefer the text in the reader's language when the API has one for it. */
export function localText(
  english: string | null | undefined,
  local: string | null | undefined,
  localLang: string | null | undefined,
  uiLang: string,
): string {
  if (local && localLang && localLang.split('-')[0] === uiLang) return local;
  return english ?? '';
}

export function cls(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

const SCRIPT: Record<string, RegExp> = {
  ja: /[\u3040-\u30ff\u4e00-\u9fff]/,
  hi: /[\u0900-\u097f]/,
  ml: /[\u0d00-\u0d7f]/,
  ar: /[\u0600-\u06ff]/,
};

/** Whether a text is written in the script of a UI language (for showing a local label only to its readers). */
export function inLanguage(text: string | null | undefined, locale: string): boolean {
  if (!text) return false;
  const rx = SCRIPT[locale];
  return rx ? rx.test(text) : false;
}

/** "Inheritance procedure at Naniwa Bank (浪速銀行 相続手続)" in Japanese is "浪速銀行 相続手続". */
export function localPart(title: string, locale: string): string | null {
  const m = title.match(/[(（]([^)）]+)[)）]\s*$/);
  return m && inLanguage(m[1], locale) ? m[1].trim() : null;
}

/** An institution's name for this page: its own name when that is written in the page's script (浪速銀行 on a
 * Japanese page), otherwise its short English name. */
export function institutionLabel(inst: { name: string; short_name?: string | null } | null | undefined, locale: string): string {
  if (!inst) return '';
  return inLanguage(inst.name, locale) ? inst.name : (inst.short_name ?? inst.name);
}

/** "A, B and C" in the page's language (A、B、C in Japanese). */
export function listOf(items: string[], locale: string): string {
  try {
    return new Intl.ListFormat(intl(locale), { style: 'long', type: 'conjunction' }).format(items);
  } catch {
    return items.join(', ');
  }
}

type Named = { name?: string | null; display_name?: string | null; name_en?: string | null };

/** Who approved, when the screen that approved did not say which family member: the API's "the family". */
export const FAMILY = 'family';

/** A person's name for this page: in their own script when the page is in that script (佐藤 大輔 on a Japanese
 * page), otherwise the short form of their English name. Names are never translated. */
export function personName(p: Named | null | undefined, locale: string): string {
  if (!p) return '';
  const own = p.name ?? p.display_name ?? '';
  return own && inLanguage(own, locale) ? own : shortName(p.name_en ?? own);
}

/** The name to address someone by: their own-script name on a page in that script, else their first name. */
export function personFirst(p: Named | null | undefined, locale: string): string {
  if (!p) return '';
  const own = p.name ?? p.display_name ?? '';
  if (own && inLanguage(own, locale)) return own;
  const full = (p.name_en ?? own).trim();
  const first = full.split(/\s+/)[0];
  return /^\p{L}\.?$/u.test(first) ? full : first;
}

/** A text closed as a sentence in its own script: "…に決めます" gets 。, "…the claim" gets ., and a text that already
 * ends a sentence is left alone. */
export function sentence(text: string | null | undefined): string {
  const t = (text ?? '').trim();
  if (!t || /[.!?。！？।؟]$/.test(t)) return t;
  return t + (/[\u3040-\u30ff\u4e00-\u9fff]$/.test(t) ? '。' : /[\u0900-\u097f]$/.test(t) ? '।' : '.');
}

/** A whole number in the reader's digits and grouping (tokens, calls). */
export function count(n: number | null | undefined, locale: string): string {
  return new Intl.NumberFormat(intl(locale)).format(n ?? 0);
}

/** An estimated model cost in US dollars: three decimals below ten cents, so a step's cost is not shown as $0.00. */
export function usd(amount: number | null | undefined, locale: string): string {
  const v = amount ?? 0;
  const digits = v > 0 && v < 0.1 ? 3 : 2;
  return new Intl.NumberFormat(intl(locale), {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(v);
}

/** A page that exists on the web. The sample cases' institutions are fictional and their "pages" sit on reserved
 * names (example, invalid, test), which would open an error page; they are shown without a link. */
export function openable(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    const host = new URL(url).hostname.toLowerCase();
    return !/(^|\.)(example|invalid|test|localhost)$/.test(host) && !/(^|\.)example\.(com|org|net)$/.test(host);
  } catch {
    return false;
  }
}
