'use client';
/** Starting a case: which language to speak in, then straight into telling the story. */
import { useLocale, useTranslations } from 'next-intl';
import { useMemo, useState, type FormEvent } from 'react';
import { toast } from 'sonner';
import { LanguageSelect } from '@/components/CaseShell';
import { Button, Logo } from '@/components/ui';
import { Link, useRouter } from '@/i18n/navigation';
import { locales, type Locale } from '@/i18n/routing';
import { intl } from '@/lib/format';
import { useStartCase } from '@/lib/queries';
import { rememberCase } from '@/lib/mycases';
import { SignInFirst, useMounted } from '@/components/Account';
import { useAuth } from '@/lib/auth';
import { useSite } from '@/lib/site';

// The app's own languages first, then languages the agents speak in a live run.
const MORE = [
  'ta',
  'te',
  'bn',
  'mr',
  'gu',
  'kn',
  'pa',
  'ur',
  'zh',
  'ko',
  'vi',
  'id',
  'ms',
  'th',
  'tl',
  'fr',
  'de',
  'pt',
  'it',
  'ru',
  'tr',
  'pl',
  'nl',
  'sw',
];

export function NewCaseScreen() {
  const t = useTranslations('newCase');
  const site = useSite();
  const mounted = useMounted();
  const signedIn = useAuth((s) => s.user !== null);
  const locale = useLocale();
  const router = useRouter();
  const start = useStartCase();
  const [lang, setLang] = useState<string>(locale);
  const [name, setName] = useState('');
  const names = useMemo(() => {
    const dn = new Intl.DisplayNames([intl(locale)], { type: 'language' });
    const own = (code: string) => {
      try {
        return new Intl.DisplayNames([code], { type: 'language' }).of(code) ?? code;
      } catch {
        return code;
      }
    };
    return [...locales, ...MORE].map((code) => ({ code, label: `${own(code)}${own(code) !== dn.of(code) ? ` · ${dn.of(code)}` : ''}` }));
  }, [locale]);

  function submit(e: FormEvent) {
    e.preventDefault();
    start.mutate(
      { lang, starter_name: name.trim() || null },
      {
        onSuccess: (out) => {
          rememberCase(out.case.id);
          const ui = (locales as readonly string[]).includes(lang) ? (lang as Locale) : (locale as Locale);
          router.push(`/cases/${out.case.id}/story`, { locale: ui });
        },
        onError: (err) => toast.error(err.message),
      },
    );
  }

  return (
    <div className="flex min-h-screen flex-col bg-paper">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-rule bg-sheet px-4 py-3.5 sm:px-8">
        <Link href="/" className="flex items-center gap-3 text-ink no-underline">
          <Logo size={26} />
          <span className="font-serif text-[20px] font-semibold">AfterLedger</span>
        </Link>
        <LanguageSelect compact />
      </header>
      <main id="main" className="mx-auto flex w-full max-w-[640px] flex-1 flex-col justify-center gap-6 px-4 py-10">
        <div>
          <h1 className="m-0 font-serif text-[34px] font-semibold leading-tight">{t('title')}</h1>
          <p className="m-0 mt-2 text-[18px] text-ink-soft">{t('lede')}</p>
        </div>
        {site.auth && (!mounted || !signedIn) ? (
          mounted ? (
            <SignInFirst next="/new" />
          ) : null
        ) : (
          <form onSubmit={submit} className="flex flex-col gap-4 rounded-[16px] border border-rule bg-sheet p-6">
            <label className="flex flex-col gap-1.5 text-[16px] font-bold">
              {t('inLanguage')}
              <select
                value={lang}
                onChange={(e) => setLang(e.target.value)}
                className="min-h-12 rounded-[10px] border border-rule-strong bg-sheet px-3 text-[17px] font-normal"
              >
                {names.map((n) => (
                  <option key={n.code} value={n.code} lang={n.code}>
                    {n.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1.5 text-[16px] font-bold">
              {t('yourName')}
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoComplete="name"
                className="min-h-12 rounded-[10px] border border-rule-strong bg-sheet px-3 text-[17px] font-normal"
              />
              <span className="text-[14px] font-normal text-ink-faint">{t('yourNameHint')}</span>
            </label>
            <Button type="submit" size="lg" disabled={start.isPending} className="self-start">
              {start.isPending ? t('starting') : t('start')}
            </Button>
          </form>
        )}
        <p className="m-0 text-[15px] text-ink-faint">{t('privacy')}</p>
      </main>
    </div>
  );
}
