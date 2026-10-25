'use client';
import { Check } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useMemo, useState, useSyncExternalStore } from 'react';
import { LanguageSelect } from '@/components/CaseShell';
import { ChallengeForm } from '@/components/ChallengeForm';
import { Button, ButtonLink, Chip, ErrorNote, Logo, ScrollX, Skeleton, SlowNote, Wordmark } from '@/components/ui';
import { Link } from '@/i18n/navigation';
import { perform } from '@/lib/changes';
import { caseTitle, countryName, longDate } from '@/lib/format';
import { myCasesSnapshot, onMyCasesChange } from '@/lib/mycases';
import { useCases, useDeleteCase, useDeleteQuestion, useFamilies, useMyQuestions } from '@/lib/queries';
import { useSite } from '@/lib/site';
import { WhyAfterLedger } from './WhyAfterLedger';
import { AccountLinks } from '@/components/Account';

const STATEMENT = [
  ['01-05-26', 'PENSION UP TREASURY LKO', '', '38,450.00', '1,62,318.40'],
  ['05-05-26', 'ACH D- NACH MF SIP CAMS 4471', '5,000.00', '', '1,57,318.40'],
  ['12-05-26', 'UPI/AMIT VERMA/PUNE', '3,000.00', '', '1,54,318.40'],
  ['27-05-26', 'PMJJBY PREM RENEWAL 2026-27', '436.00', '', '1,53,882.40'],
  ['30-05-26', 'UPPCL ELEC BILL LKO', '1,284.00', '', '1,52,598.40'],
];

const h2 = 'm-0 font-serif text-[32px] font-semibold leading-[1.12] tracking-[-0.015em] sm:text-[40px]';

/** The cases this browser started or opened (a new case, one saved from the live challenge, or one shared with
 * this family member), so they can be found again; shown only once there is one. Each can be deleted, after
 * asking. On the public site nobody sees anyone else's cases: there are no accounts, and a case's random id is its
 * private address. */
function YourCases() {
  const t = useTranslations('main');
  const tc = useTranslations('common');
  const locale = useLocale();
  const snapshot = useSyncExternalStore(onMyCasesChange, myCasesSnapshot, () => null);
  const ids = useMemo(() => (snapshot === null ? null : snapshot.split(',').filter(Boolean)), [snapshot]);
  const cases = useCases(ids);
  const remove = useDeleteCase();
  const [asking, setAsking] = useState<string | null>(null);
  const own = (cases.data ?? []).filter((c) => !c.demo_key);
  if (!own.length) return null;
  return (
    <section id="yours" aria-labelledby="yours-title" className="border-t border-rule py-12">
      <h2 id="yours-title" className={h2}>
        {t('yourCases')}
      </h2>
      <ul className="m-0 mt-6 grid list-none gap-3 p-0 md:grid-cols-2 lg:grid-cols-3">
        {own.map((c) => {
          const where = [c.died_in, c.country_codes[0] ? countryName(c.country_codes[0], locale) : null].filter(Boolean).join(', ');
          return (
            <li key={c.id} className="flex flex-col gap-1.5 rounded-[14px] border border-rule bg-sheet px-5 py-4">
              <h3 className="m-0 font-serif text-[20px] font-semibold leading-tight">{caseTitle(c.title) || tc('newCase')}</h3>
              <p className="m-0 text-[15px] text-ink-soft">
                {[c.died_on ? longDate(c.died_on, locale) : null, where || null, c.stage === 'intake' ? t('stillTelling') : null]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
              {asking === c.id ? (
                <div role="group" aria-label={t('deleteCase')} className="mt-1 flex flex-col gap-2.5 rounded-xl bg-paper p-3 text-[15px]">
                  <p className="m-0">{t('deleteAsk')}</p>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="danger"
                      disabled={remove.isPending}
                      onClick={() => perform(remove, c.id, { done: () => t('deleted'), after: () => setAsking(null) })}
                    >
                      {t('deleteYes')}
                    </Button>
                    <Button size="sm" variant="secondary" disabled={remove.isPending} onClick={() => setAsking(null)}>
                      {tc('cancel')}
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="mt-1 flex flex-wrap items-center gap-x-5 gap-y-2">
                  <ButtonLink href={c.stage === 'intake' ? `/cases/${c.id}/story` : `/cases/${c.id}/today`} size="sm">
                    {t('openCase')}
                  </ButtonLink>
                  <button type="button" onClick={() => setAsking(c.id)} className="tap text-[15px] font-bold text-ink-soft hover:underline">
                    {t('deleteCase')}
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function MainScreen() {
  const t = useTranslations('main');
  const tc = useTranslations('common');
  const tn = useTranslations('nav');
  const families = useFamilies();
  const site = useSite();
  const first = families.data?.find((f) => f.key === 'verma') ?? families.data?.[0];
  const demoHref = first ? `/cases/${first.case_id}/today` : '/';

  return (
    <div id="top" className="min-h-screen bg-paper">
      <div className="mx-auto max-w-[1240px] px-5 sm:px-8">
        <header className="flex flex-wrap items-center justify-between gap-6 border-b border-rule py-[22px]">
          <a href="#top" className="flex items-center gap-2.5 text-ink no-underline">
            <Logo />
            <Wordmark />
          </a>
          <nav aria-label={tn('main')} className="flex flex-wrap items-center gap-x-7 gap-y-2 text-[16px]">
            <a
              href="#how"
              className="text-ink no-underline hover:underline pointer-coarse:inline-flex pointer-coarse:min-h-11 pointer-coarse:items-center"
            >
              {t('howItWorks')}
            </a>
            <a
              href="#families"
              className="text-ink no-underline hover:underline pointer-coarse:inline-flex pointer-coarse:min-h-11 pointer-coarse:items-center"
            >
              {t('demoCases')}
            </a>
            <a
              href="#challenge"
              className="text-ink no-underline hover:underline pointer-coarse:inline-flex pointer-coarse:min-h-11 pointer-coarse:items-center"
            >
              {t('anyCountry')}
            </a>
            {site.product ? null : (
              <Link
                href="/evaluation"
                className="text-ink no-underline hover:underline pointer-coarse:inline-flex pointer-coarse:min-h-11 pointer-coarse:items-center"
              >
                {t('evaluation')}
              </Link>
            )}
            <AccountLinks />
            <LanguageSelect />
          </nav>
        </header>
        <main id="main">
          <section className="grid items-center gap-16 py-16 sm:py-[72px] lg:pb-[88px] [grid-template-columns:repeat(auto-fit,minmax(min(500px,100%),1fr))]">
            <div>
              <h1 className="m-0 font-serif text-[40px] font-semibold leading-[1.06] tracking-[-0.02em] sm:text-[60px]">
                {t('heroTitle')}
              </h1>
              <p className="m-0 mt-6 max-w-[33em] text-[20px] leading-[1.55] text-ink-soft">{t('heroText')}</p>
              <div className="mt-9 flex flex-wrap items-center gap-6">
                <ButtonLink href={demoHref} size="lg">
                  {t('openDemo')}
                </ButtonLink>
                <a href="#challenge" className="tap text-[18px] font-bold">
                  {t('orCountry')}
                </a>
              </div>
              <p className="m-0 mt-5 text-[15px] text-ink-faint">{t('noAccount')}</p>
              <p className="m-0 mt-2 text-[15px]">
                <Link href="/new" className="tap font-bold">
                  {t('startOwn')}
                </Link>
              </p>
            </div>
            <figure className="relative m-0">
              <div className="overflow-hidden rounded-[14px] border border-rule bg-sheet shadow-[var(--shadow-sheet)]">
                <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-rule px-[22px] py-4">
                  <span className="text-[17px] font-bold">{t('statementTitle')}</span>
                  <span className="text-[14px] text-ink-faint">{t('statementSub')}</span>
                </div>
                <ScrollX label={t('statementTitle')}>
                  <table className="w-full border-collapse text-[14.5px] tabular">
                    <thead>
                      <tr className="text-start text-ink-faint">
                        <th scope="col" className="border-b-[1.5px] border-rule-strong py-2.5 pe-3 ps-[22px] text-start font-semibold">
                          {t('colDate')}
                        </th>
                        <th scope="col" className="border-b-[1.5px] border-rule-strong px-3 py-2.5 text-start font-semibold">
                          {t('colNarration')}
                        </th>
                        <th scope="col" className="double-rule border-b-[1.5px] border-rule-strong px-3 py-2.5 text-end font-semibold">
                          {t('colDebit')}
                        </th>
                        <th scope="col" className="border-b-[1.5px] border-rule-strong px-3 py-2.5 text-end font-semibold">
                          {t('colCredit')}
                        </th>
                        <th scope="col" className="border-b-[1.5px] border-rule-strong py-2.5 pe-[22px] ps-3 text-end font-semibold">
                          {t('colBalance')}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {STATEMENT.map(([d, n, dr, cr, bal], i) => {
                        const hit = n.startsWith('PMJJBY');
                        return (
                          <tr key={i}>
                            <td className="whitespace-nowrap border-b border-rule py-2 pe-3 ps-[22px]">{d}</td>
                            <td className="border-b border-rule px-3 py-2">{hit ? <span className="swipe font-bold">{n}</span> : n}</td>
                            <td className="double-rule border-b border-rule px-3 py-2 text-end">
                              {hit ? <span className="swipe font-bold">{dr}</span> : dr}
                            </td>
                            <td className="border-b border-rule px-3 py-2 text-end">{cr}</td>
                            <td className="border-b border-rule py-2 pe-[22px] ps-3 text-end">{bal}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </ScrollX>
                <div className="border-t border-rule bg-foot px-[22px] py-2 text-[12.5px] text-ink-faint">{t('syntheticDoc')}</div>
              </div>
              <figcaption className="reveal relative mt-[18px] max-w-[470px] rounded-[14px] border-[1.5px] border-ledger bg-sheet px-[22px] pb-5 pt-[18px] shadow-[var(--shadow-pop)] sm:ms-12">
                <span
                  aria-hidden="true"
                  className="absolute -top-[9px] start-10 h-4 w-4 rotate-45 border-s-[1.5px] border-t-[1.5px] border-ledger bg-sheet"
                />
                <div className="flex items-center gap-2 text-[15px] font-bold text-ledger">
                  <Check size={18} strokeWidth={2.4} aria-hidden="true" />
                  <span>{t('foundTitle')}</span>
                </div>
                <p className="m-0 mt-2 text-[17px] leading-normal">{t('foundText')}</p>
                <div className="mt-3.5 flex flex-wrap gap-2 text-[14px]">
                  <Chip tone="ledger">{tc('sure')}</Chip>
                  <Chip>{t('foundSources')}</Chip>
                  <Chip>{t('foundLine')}</Chip>
                </div>
              </figcaption>
            </figure>
          </section>

          <YourCases />

          <section id="families" className="border-t border-rule py-[72px]">
            <div className="max-w-[640px]">
              <h2 className={h2}>{t('familiesTitle')}</h2>
              <p className="m-0 mt-3.5 text-[18px] text-ink-soft">{t('familiesText')}</p>
            </div>
            {families.isError ? (
              <div className="mt-6">
                <ErrorNote error={families.error} retry={() => families.refetch()} />
              </div>
            ) : (
              <SlowNote active={families.isPending} className="mt-6" />
            )}
            {/* phones and tablets: a card per family */}
            <ul className="m-0 mt-8 grid list-none gap-3 p-0 md:grid-cols-2 lg:hidden">
              {families.data
                ? families.data.map((f) => (
                    <li key={f.key} className="flex flex-col gap-2 rounded-[14px] border border-rule bg-sheet px-5 py-4">
                      <h3 className="m-0 font-serif text-[21px] font-semibold leading-tight">{f.family_label}</h3>
                      <p className="m-0 text-[15px] text-ink-soft">
                        {f.where} · {f.spoken}
                      </p>
                      <p className="m-0 text-[16px]">{f.shows}</p>
                      {f.seeded ? (
                        <div className="mt-1 flex flex-wrap items-center gap-x-5 gap-y-2">
                          <ButtonLink href={`/cases/${f.case_id}/today`} size="sm">
                            {t('openCase')}
                          </ButtonLink>
                        </div>
                      ) : (
                        <p className="m-0 mt-1 text-[15px] text-ink-faint">{t('preparing')}</p>
                      )}
                    </li>
                  ))
                : [0, 1, 2].map((i) => (
                    <li key={i}>
                      <Skeleton className="h-36" />
                    </li>
                  ))}
            </ul>
            {/* wide screens: the table; relative, so its hidden column label stays inside the scroller */}
            <div className="relative mt-9 hidden overflow-x-auto rounded-[14px] border border-rule bg-sheet lg:block">
              <table className="w-full min-w-[860px] border-collapse text-[16px]">
                <thead>
                  <tr className="text-[14.5px] text-ink-faint">
                    <th scope="col" className="border-b-[1.5px] border-rule-strong px-6 py-3.5 text-start font-semibold">
                      {t('colFamily')}
                    </th>
                    <th scope="col" className="border-b-[1.5px] border-rule-strong px-4 py-3.5 text-start font-semibold">
                      {t('colWhere')}
                    </th>
                    <th scope="col" className="border-b-[1.5px] border-rule-strong px-4 py-3.5 text-start font-semibold">
                      {t('colSpoken')}
                    </th>
                    <th scope="col" className="border-b-[1.5px] border-rule-strong px-4 py-3.5 text-start font-semibold">
                      {t('colShows')}
                    </th>
                    <th scope="col" className="border-b-[1.5px] border-rule-strong px-6 py-3.5">
                      <span className="sr-only">{t('openCase')}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {families.data
                    ? families.data.map((f, i) => (
                        <tr key={f.key}>
                          <th
                            scope="row"
                            className={`min-w-[8.5em] px-6 py-[18px] text-start font-serif text-[19px] font-semibold ${i < families.data!.length - 1 ? 'border-b border-rule' : ''}`}
                          >
                            {f.family_label}
                          </th>
                          <td className={`px-4 py-[18px] ${i < families.data!.length - 1 ? 'border-b border-rule' : ''}`}>{f.where}</td>
                          <td className={`px-4 py-[18px] ${i < families.data!.length - 1 ? 'border-b border-rule' : ''}`}>{f.spoken}</td>
                          <td className={`px-4 py-[18px] text-ink-soft ${i < families.data!.length - 1 ? 'border-b border-rule' : ''}`}>
                            {f.shows}
                          </td>
                          <td
                            className={`whitespace-nowrap px-6 py-[18px] text-end ${i < families.data!.length - 1 ? 'border-b border-rule' : ''}`}
                          >
                            {f.seeded ? (
                              <span className="inline-flex items-center gap-4">
                                <Link href={`/cases/${f.case_id}/today`} className="tap font-bold">
                                  {t('openCase')}
                                </Link>
                              </span>
                            ) : (
                              <span className="text-ink-faint">{t('preparing')}</span>
                            )}
                          </td>
                        </tr>
                      ))
                    : [0, 1, 2].map((i) => (
                        <tr key={i}>
                          <td colSpan={5} className="px-6 py-4">
                            <Skeleton className="h-8" />
                          </td>
                        </tr>
                      ))}
                </tbody>
              </table>
            </div>
          </section>

          <section id="how" className="border-t border-rule py-[72px]">
            <h2 className={h2}>{t('howItWorks')}</h2>
            <div className="mt-6">
              <WhyAfterLedger />
            </div>
            <h3 className="m-0 mt-14 font-serif text-[28px] font-semibold leading-tight sm:text-[32px]">{t('howTitle')}</h3>
            <p className="m-0 mt-3.5 max-w-[46em] text-[18px] text-ink-soft">{t('howText')}</p>
            <ol className="m-0 mt-10 grid list-none border-t-2 border-ink p-0 [grid-template-columns:repeat(auto-fit,minmax(min(124px,100%),1fr))]">
              {(['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9'] as const).map((s, i) => (
                <li key={s} className={`pt-[18px] ${i === 0 ? 'pe-4' : i === 8 ? 'ps-4' : 'px-4'} ${i < 8 ? 'border-e border-rule' : ''}`}>
                  <div className="font-serif text-[30px] font-semibold text-ledger">{i + 1}</div>
                  <div className="mt-1 font-bold">{t(`stages.${s}.name`)}</div>
                  <p className="m-0 mt-1.5 text-[15px] text-ink-soft">{t(`stages.${s}.text`)}</p>
                  {t(`stages.${s}.you`) ? (
                    <p className="m-0 mt-3 rounded-lg border-[1.5px] border-ledger px-2 py-1.5 text-[14px] font-bold text-ledger">
                      {t(`stages.${s}.you`)}
                    </p>
                  ) : null}
                </li>
              ))}
            </ol>
            <p className="m-0 mt-7 text-[16px] text-ink-soft">{t('howFoot')}</p>
          </section>

          <section
            id="sources"
            className="grid items-start gap-14 border-t border-rule py-[72px] [grid-template-columns:repeat(auto-fit,minmax(min(460px,100%),1fr))]"
          >
            <div>
              <h2 className={h2}>{t('sourcesTitle')}</h2>
              <p className="m-0 mt-4 max-w-[30em] text-[18px] text-ink-soft">{t('sourcesText1')}</p>
              <p className="m-0 mt-4 max-w-[30em] text-[18px] text-ink-soft">{t('sourcesText2')}</p>
            </div>
            <div className="rounded-[14px] border border-rule bg-sheet px-7 py-[26px]">
              <p className="m-0 text-[18px] font-bold">{t('exampleRule')}</p>
              <blockquote lang="en" className="m-0 mt-[18px] border-s-[3px] border-hl py-1 ps-[18px] font-serif text-[18px] leading-[1.55]">
                {t('exampleQuote')}
              </blockquote>
              <p className="m-0 mt-4 text-[15px] text-ink-soft">{t('exampleSource')}</p>
              <div className="mt-3.5 flex flex-wrap gap-2 text-[14px]">
                <Chip tone="ledger">{tc('level.dual_verified')}</Chip>
                <Chip>{tc('tier.Regulator')}</Chip>
                <Chip>{t('exampleChecked')}</Chip>
              </div>
            </div>
          </section>

          <section id="challenge" className="mb-[72px] rounded-[18px] bg-night p-6 text-[#F2F5F3] sm:p-12">
            <h2 className={h2}>{t('challengeTitle')}</h2>
            <p className="m-0 mt-3.5 max-w-[44em] text-[18px] text-night-soft">{t('challengeText')}</p>
            <ChallengeForm />
            <MyQuestions />
          </section>
        </main>

        <footer className="grid gap-8 border-t border-rule pb-12 pt-8 text-[15px] text-ink-soft [grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr))]">
          <p className="m-0">{t('footer1')}</p>
          <p className="m-0">{t('footer2')}</p>
          <div className="flex flex-wrap items-start justify-end gap-5">
            <ButtonLink href={demoHref}>{t('openDemo')}</ButtonLink>
            {site.product ? null : (
              <Link href="/evaluation" className="inline-flex min-h-[46px] items-center font-bold">
                {t('measured')}
              </Link>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}

/** The questions this account asked: each keeps researching on the server when its page is left, and each can be
 * removed from the list, after asking (research a case's claim needed stays with the claim). */
function MyQuestions() {
  const t = useTranslations('main');
  const tc = useTranslations('common');
  const locale = useLocale();
  const q = useMyQuestions();
  const remove = useDeleteQuestion();
  const [asking, setAsking] = useState<string | null>(null);
  const rows = q.data ?? [];
  if (!rows.length) return null;
  const state = (status: string) =>
    status === 'running' ? t('questionRunning') : status === 'error' ? t('questionStopped') : t('questionDone');
  return (
    <div className="mt-12 border-t border-white/15 pt-8">
      <h3 className="m-0 text-[22px] font-bold">{t('myQuestions')}</h3>
      <p className="m-0 mt-2 max-w-[44em] text-[16px] text-night-soft">{t('myQuestionsNote')}</p>
      <ul className="m-0 mt-5 grid list-none gap-3 p-0">
        {rows.map((r) => (
          <li key={r.id} className="rounded-[14px] border border-white/20 px-5 py-4 focus-within:border-white/50 hover:border-white/50">
            <Link href={`/challenge/${encodeURIComponent(r.id)}`} className="tap block text-[#F2F5F3] no-underline">
              <span className="block text-[14px] text-night-faint">
                {[countryName(r.country, locale), state(r.status), r.shared ? t('questionForClaim') : null, longDate(r.started_at, locale)]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
              <span dir="auto" className="mt-1 line-clamp-2 block text-[17px] leading-snug">
                {r.question}
              </span>
            </Link>
            {r.shared ? null : asking === r.id ? (
              <div
                role="group"
                aria-label={t('removeQuestion')}
                className="mt-3 flex flex-col gap-2.5 rounded-xl bg-white/10 p-3 text-[15px]"
              >
                <p className="m-0">{t('removeQuestionAsk')}</p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={remove.isPending}
                    onClick={() => perform(remove, r.id, { done: () => t('questionRemoved'), after: () => setAsking(null) })}
                  >
                    {t('removeQuestionYes')}
                  </Button>
                  <Button size="sm" variant="secondary" disabled={remove.isPending} onClick={() => setAsking(null)}>
                    {tc('cancel')}
                  </Button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setAsking(r.id)}
                className="tap mt-2 text-[15px] font-bold text-night-soft underline-offset-4 hover:text-[#F2F5F3] hover:underline"
              >
                {t('removeQuestion')}
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
