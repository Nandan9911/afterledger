'use client';
/** "Name any country": the live challenge form with the prepared questions, used on the main page and /challenge. */
import type { ChallengeIn } from '@afterledger/contracts';
import { useLocale, useTranslations } from 'next-intl';
import { useState } from 'react';
import { usePathname, useRouter } from '@/i18n/navigation';
import { useAuth } from '@/lib/auth';
import { perform } from '@/lib/changes';
import { useMeta, usePresets, useStartChallenge } from '@/lib/queries';
import { useSite } from '@/lib/site';
import { CountryInput } from './CountryInput';
import { buttonClass } from './ui';

export function ChallengeForm() {
  const t = useTranslations('main');
  const locale = useLocale();
  const router = useRouter();
  const meta = useMeta();
  const presets = usePresets();
  const start = useStartChallenge();
  const site = useSite();
  const pathname = usePathname();
  const signedIn = useAuth((s) => s.user !== null);
  const [country, setCountry] = useState('FR');
  const [institution, setInstitution] = useState('');
  const [situation, setSituation] = useState('');
  const [question, setQuestion] = useState('');

  function go(body: ChallengeIn) {
    if (site.auth && !signedIn) {
      // a question is researched for an account, so it can be kept and saved as a case
      router.push(`/signin?next=${encodeURIComponent(pathname)}`);
      return;
    }
    perform(start, body, { done: (run) => router.push(`/challenge/${run.id}`) });
  }

  const field = 'min-h-[52px] rounded-[10px] border-0 bg-white px-3.5 text-[18px] text-ink';
  return (
    <>
      <form
        className="mt-8 grid items-end gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(220px,100%),1fr))]"
        onSubmit={(e) => {
          e.preventDefault();
          go({
            country,
            question: question || situation || t('challengeTitle'),
            institution: institution || null,
            situation: situation || null,
            lang: null,
          });
        }}
      >
        <label className="flex flex-col gap-2 text-[15px] text-night-soft">
          {t('country')}
          <CountryInput value={country} onChange={setCountry} locale={locale} />
        </label>
        <label className="flex flex-col gap-2 text-[15px] text-night-soft">
          {t('institution')}
          <input
            value={institution}
            onChange={(e) => setInstitution(e.target.value)}
            placeholder={t('institutionHint')}
            className={field}
          />
        </label>
        <label className="flex flex-col gap-2 text-[15px] text-night-soft">
          {t('situation')}
          <input value={situation} onChange={(e) => setSituation(e.target.value)} className={field} />
        </label>
        <label className="col-span-full flex flex-col gap-2 text-[15px] text-night-soft">
          {t('question')}
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={2}
            className="rounded-[10px] border-0 bg-white px-3.5 py-3 text-[18px] text-ink"
          />
        </label>
        <button type="submit" disabled={start.isPending} className={buttonClass('highlight', 'lg')}>
          {start.isPending ? t('starting') : t('start')}
        </button>
      </form>
      {presets.data?.length ? (
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <span className="text-[15px] text-night-faint">{t('presets')}</span>
          {presets.data.map((p) => (
            <button
              key={p.key}
              type="button"
              disabled={start.isPending}
              onClick={() =>
                go({
                  country: p.country,
                  question: p.question,
                  lang: p.lang,
                  institution: p.institution,
                  situation: p.situation,
                  holding_kind: p.holding_kind,
                  title: p.title ?? null,
                  asked_by: p.asked_by ?? null,
                  translation: p.translation || null,
                })
              }
              className="rounded-full border border-white/25 px-3.5 py-1.5 text-start text-[15px] text-white hover:bg-white/10"
            >
              <span className="font-bold">{p.country_name}</span>
              <span lang={p.lang} className="ms-2 text-night-soft">
                {p.question.length > 60 ? `${p.question.slice(0, 60)}…` : p.question}
              </span>
            </button>
          ))}
        </div>
      ) : null}
      <p className="m-0 mt-5 text-[15px] text-night-faint">
        {t('countriesNote', { count: meta.data?.countries_researched ?? 6 })}
        {meta.data?.llm === 'scripted' ? ` ${t('offlineNote')}` : ''}
      </p>
    </>
  );
}
