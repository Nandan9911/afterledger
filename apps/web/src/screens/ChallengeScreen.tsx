'use client';
/**
 * The live challenge. A question about any country is researched while the person watches: a first answer
 * from one official page, two researchers working apart, a citation check that drops any quote not found
 * word for word on its page, an adjudicator, a plan compiled and checked by code, and a letter in the
 * institution's own language. When no official source exists, it says so and starts by asking in writing.
 */
import type { ChallengeView, CiteView } from '@afterledger/contracts';
import { useLocale, useTranslations } from 'next-intl';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { ChallengeForm } from '@/components/ChallengeForm';
import { LanguageSelect } from '@/components/CaseShell';
import { Button, ButtonLink, Chip, ErrorNote, Logo, Skeleton, Spinner } from '@/components/ui';
import { ApiError } from '@/lib/api';
import { Why } from '@/components/Why';
import { Link, useRouter } from '@/i18n/navigation';
import { perform } from '@/lib/changes';
import { useEventStream } from '@/lib/events';
import { asDate, cls, countryName, intl, languageName } from '@/lib/format';
import { useChallenge, useSaveChallenge } from '@/lib/queries';
import { rememberCase } from '@/lib/mycases';

type Ev = {
  n: number;
  at: number;
  stage: string;
  text: string;
  who?: string;
  status?: string;
  url?: string;
  query?: string;
  lang?: string;
};
type Step = { id: string; text: string; cites?: string[]; when?: Cond | null; actor?: string };
type Cond = { op: string; var?: string; value?: unknown; args?: Cond[] };

const OPS: Record<string, string> = { lt: 'under', le: 'at most', gt: 'over', ge: 'at least', eq: 'is', ne: 'is not' };

function condText(c: Cond | null | undefined): string {
  if (!c) return '';
  if ((c.op === 'and' || c.op === 'or') && c.args?.length)
    return c.args
      .map(condText)
      .filter(Boolean)
      .join(c.op === 'and' ? ' and ' : ' or ');
  if (c.op === 'not' && c.args?.length) return `not ${condText(c.args[0])}`;
  const v = (c.var ?? '').replaceAll('_', ' ');
  if (c.op === 'truthy') return v;
  return `${v} ${OPS[c.op] ?? c.op} ${String(c.value ?? '')}`.trim();
}

function clock(started: string | null | undefined, at: number | undefined, locale: string) {
  const d = asDate(started);
  if (!d || at === undefined) return '';
  return new Intl.DateTimeFormat(intl(locale), { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(
    new Date(d.getTime() + at * 1000),
  );
}

function TopBar({ finished, running }: { finished: boolean; running: boolean }) {
  const t = useTranslations('challenge');
  return (
    <header className="flex flex-wrap items-center justify-between gap-4 border-b border-rule bg-sheet px-4 py-3.5 sm:px-8">
      <div className="flex items-center gap-3">
        <Link href="/" className="flex items-center gap-3 text-ink no-underline" aria-label="AfterLedger">
          <Logo size={26} />
          <span className="font-serif text-[20px] font-semibold">AfterLedger</span>
        </Link>
        <span className="border-s border-rule ps-3 text-ink-faint">{t('title')}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2.5">
        {finished ? (
          <span className="rounded-full bg-hl px-2.5 py-0.5 text-[14px] font-bold text-ink">{t('researchedNow')}</span>
        ) : running ? (
          <span className="inline-flex items-center gap-2 rounded-full border-[1.5px] border-dotted border-blank px-2.5 py-0.5 text-[14px] font-bold text-blank">
            <span className="h-2 w-2 animate-pulse-soft rounded-full bg-blank" aria-hidden="true" />
            {t('running')}
          </span>
        ) : null}
        <LanguageSelect compact />
        <ButtonLink href="/challenge" variant="secondary">
          {t('tryAnother')}
        </ButtonLink>
      </div>
    </header>
  );
}

function FirstAnswer({ view, seen, planSeen }: { view: ChallengeView; seen: boolean; planSeen: boolean }) {
  const t = useTranslations('challenge');
  const locale = useLocale();
  const fa = view.first_answer as null | {
    answer?: string;
    answer_en?: string;
    lang?: string;
    found_official?: boolean;
    general?: string[];
    general_en?: string[];
    cites?: string[];
  };
  if (!seen || !fa) {
    return (
      <div className="flex min-h-40 items-center justify-center rounded-[14px] border border-dashed border-rule-strong bg-sheet p-5">
        <Spinner label={t('firstAnswerPending')} />
      </div>
    );
  }
  const lang = (fa.lang ?? 'en').split('-')[0];
  const showEn = lang !== locale && fa.answer_en;
  const general = lang === locale ? fa.general : fa.general_en?.length ? fa.general_en : fa.general;
  if (!fa.found_official) {
    return (
      <div className="flex flex-col gap-3">
        <section className="rounded-[14px] border-[1.5px] border-dotted border-blank bg-sheet px-5 py-4">
          <p className="m-0 text-[16px] font-bold text-blank">{t('noOfficial')}</p>
          <p lang={fa.lang} className="m-0 mt-1 text-[16px]">
            {fa.answer}
          </p>
          {showEn ? <p className="m-0 mt-1 text-[15px] text-ink-soft">{fa.answer_en}</p> : null}
        </section>
        {general?.length ? (
          <section className="rounded-[14px] border border-rule bg-sheet px-5 py-4">
            <div className="flex items-baseline justify-between gap-2">
              <h2 className="m-0 text-[16px] font-bold">{t('general')}</h2>
              <span className="rounded-full border-[1.5px] border-dashed border-pencil bg-pencil-tint px-2 text-[12.5px] font-bold text-pencil">
                {t('generalTag')}
              </span>
            </div>
            <ul className="m-0 mt-1.5 ps-5 text-[15.5px]">
              {general.map((g, i) => (
                <li key={i}>{g}</li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    );
  }
  const ev = (stage: string) => view.events.find((e) => (e as Ev).stage === stage) as Ev | undefined;
  return (
    <section className="rounded-[14px] border border-rule bg-sheet px-[22px] py-5">
      <p className="m-0 text-[14px] font-bold text-ink-faint">{t('firstAnswer')}</p>
      <p lang={fa.lang} className="m-0 mt-2 text-[18px]">
        {fa.answer}
      </p>
      {showEn ? <p className="m-0 mt-1.5 text-[15px] text-ink-soft">{fa.answer_en}</p> : null}
      <p className="m-0 mt-2.5 text-[14px] text-ink-faint">
        {t('times', {
          asked: clock(view.started_at, 0, locale),
          first: clock(view.started_at, ev('first_answer')?.at, locale),
          plan: (planSeen && clock(view.started_at, ev('plan')?.at ?? ev('compiler')?.at, locale)) || '…',
        })}
      </p>
    </section>
  );
}

function Checks({ view, seen }: { view: ChallengeView; seen: Set<string> }) {
  const t = useTranslations('challenge');
  const tc = useTranslations('common');
  const cols: Array<[string, string]> = [
    ['researcher_a', t('researcherA')],
    ['researcher_b', t('researcherB')],
    ['citation_check', t('citationCheck')],
    ['adjudicator', t('adjudicator')],
  ];
  return (
    <section aria-labelledby="work" className="rounded-[14px] border border-rule bg-sheet px-[22px] py-5">
      <h2 id="work" className="m-0 font-serif text-[22px] font-semibold">
        {t('checked')}
      </h2>
      <ol className="m-0 mt-4 grid list-none border-t-2 border-ink p-0 [grid-template-columns:repeat(auto-fit,minmax(min(230px,100%),1fr))]">
        {cols.map(([stage, label], i) => {
          const ev = view.events.find((e) => (e as Ev).stage === stage) as Ev | undefined;
          const on = seen.has(stage) && ev;
          let text = ev?.text ?? '';
          // the column already names the stage, so a leading "Researcher A: " (in any language) is dropped
          const colon = text.indexOf(': ');
          if (stage !== 'adjudicator' && colon > 0 && colon < 40) text = text.slice(colon + 2);
          return (
            <li key={stage} className={cls('py-3.5 pe-4 ps-0', i < cols.length - 1 && 'sm:border-e sm:border-rule', i > 0 && 'sm:ps-4')}>
              <p className="m-0 font-bold">{label}</p>
              {on ? (
                <p className="m-0 mt-0.5 text-[15px] text-ink-soft">{text.charAt(0).toUpperCase() + text.slice(1)}</p>
              ) : (
                <Spinner label={tc('working')} />
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function PlanCard({ view, cites }: { view: ChallengeView; cites: Record<string, CiteView> }) {
  const t = useTranslations('challenge');
  const tc = useTranslations('common');
  const plan = view.plan as null | {
    title?: string;
    steps?: Step[];
    level?: string;
    unverified?: boolean;
    language?: string;
    verified_on?: string;
  };
  if (!plan) return null;
  const steps = plan.steps ?? [];
  const sources = [
    ...new Set(
      steps
        .flatMap((s) => s.cites ?? [])
        .map((c) => cites[c]?.source_url)
        .filter(Boolean),
    ),
  ];
  const one = sources.length === 1 ? Object.values(cites).find((c) => c.source_url === sources[0]) : undefined;
  const gold = view.gold as null | { match?: boolean; cases?: number; agree?: number };
  const differs = Boolean(gold && gold.match === false);
  const verified = !plan.unverified && plan.level !== 'draft' && !differs;
  return (
    <section aria-labelledby="plan" className="min-w-0 flex-[3_1_560px] rounded-[14px] border border-rule bg-sheet px-6 py-[22px]">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="plan" className="m-0 font-serif text-[24px] font-semibold">
          {t('plan')}
        </h2>
        {verified ? (
          <Chip tone="ledger">{tc(`level.${plan.level ?? 'dual_verified'}` as 'level.gold')}</Chip>
        ) : (
          <Chip tone="blank">{differs ? t('notStored') : t('unverifiedPlan')}</Chip>
        )}
      </div>
      {plan.title ? <p className="m-0 mt-1 text-[15px] text-ink-soft">{plan.title}</p> : null}
      {gold?.match ? <p className="m-0 mt-1.5 text-[14px] font-bold text-ledger">{t('goldMatch', { cases: gold.cases ?? 0 })}</p> : null}
      {differs ? (
        <p className="m-0 mt-1.5 text-[14px] font-bold text-pencil">
          {t('goldDiffers', { agree: gold?.agree ?? 0, cases: gold?.cases ?? 0 })}
        </p>
      ) : null}
      <ol className="m-0 mt-3.5 list-none p-0">
        {steps.map((s, i) => {
          const first = (s.cites ?? []).map((c) => cites[c]).find(Boolean);
          const when = condText(s.when);
          return (
            <li key={s.id} className="border-t border-rule py-3.5">
              <div className="flex justify-between gap-3">
                <p className="m-0 font-bold">
                  {i + 1}. {s.text}
                </p>
                <Why ids={s.cites ?? []} cites={cites} title={s.text} />
              </div>
              {when ? <p className="m-0 mt-1 text-[14px] text-blank">{t('onlyWhen', { when })}</p> : null}
              {first ? (
                <p lang={first.language} className="m-0 mt-1.5 font-serif text-[15.5px] text-ink-soft">
                  {first.language.startsWith('fr') ? `« ${first.quote} »` : `“${first.quote}”`}
                </p>
              ) : null}
            </li>
          );
        })}
      </ol>
      {one ? (
        <p className="m-0 mt-2.5 text-[14px] text-ink-faint">
          {t('sourceFor', { source: `${one.publisher ? `${one.publisher}, ` : ''}“${one.source_title}”` })}{' '}
          {tc(`tier.${one.tier_label}` as 'tier.Regulator')}.
        </p>
      ) : null}
    </section>
  );
}

function LetterCard({ view, onChecks, checks }: { view: ChallengeView; onChecks: () => void; checks: boolean }) {
  const t = useTranslations('challenge');
  const locale = useLocale();
  const router = useRouter();
  const save = useSaveChallenge();
  const [english, setEnglish] = useState(false);
  const letter = view.letter as null | { subject?: string; body?: string; lang?: string; body_en?: string; subject_en?: string };
  if (!letter) return null;
  const lang = letter.lang ?? 'en';
  const body = english && letter.body_en ? letter.body_en : (letter.body ?? '');
  const subject = english && letter.subject_en ? letter.subject_en : letter.subject;
  return (
    <section aria-labelledby="lettre" className="min-w-0 flex-[2_1_360px] rounded-[14px] border-[1.5px] border-ink bg-sheet px-6 py-[22px]">
      <h2 id="lettre" className="m-0 font-serif text-[22px] font-semibold">
        {t('letterTo', { language: languageName(lang, locale) })}
      </h2>
      <div
        lang={english ? 'en' : lang}
        dir="auto"
        className="mt-3 rounded-[10px] border border-rule bg-[#FCFDFC] px-[18px] py-4 font-serif text-[15.5px] leading-[1.7]"
      >
        {subject ? <p className="m-0 font-semibold">{subject}</p> : null}
        {body.split(/\n\s*\n/).map((p, i) => (
          <p key={i} className={cls('m-0 whitespace-pre-line', (i > 0 || subject) && 'mt-2.5')}>
            {p}
          </p>
        ))}
      </div>
      <p className="m-0 mt-2.5 text-[15px] text-ink-soft">{t('bracketed')}</p>
      <div className="mt-3.5 flex flex-wrap gap-2.5">
        <Button
          disabled={save.isPending || !view.finished}
          onClick={() =>
            perform(save, view.id, {
              done: (out) => {
                const id = (out as { case_id?: string }).case_id;
                if (id) {
                  rememberCase(id);
                  router.push(`/cases/${id}/story`);
                }
              },
            })
          }
        >
          {save.isPending ? t('saving') : t('save')}
        </Button>
        {letter.body_en && lang !== 'en' ? (
          <Button variant="secondary" onClick={() => setEnglish((v) => !v)}>
            {english ? t('original') : t('englishCopy')}
          </Button>
        ) : null}
        <Button variant="secondary" aria-expanded={checks} onClick={onChecks}>
          {checks ? t('hideChecks') : t('openChecks')}
        </Button>
      </div>
    </section>
  );
}

function EventLog({ view }: { view: ChallengeView }) {
  const t = useTranslations('challenge');
  const locale = useLocale();
  const kept = view.kept as Array<{ id: string; quote?: string; source_url?: string; by?: string }>;
  const dropped = view.dropped as Array<{ id: string; quote?: string; source_url?: string; text?: string }>;
  const aside = [
    ...new Set([
      ...((view.researcher_a as { set_aside?: string[] }).set_aside ?? []),
      ...((view.researcher_b as { set_aside?: string[] }).set_aside ?? []),
    ]),
  ];
  return (
    <section aria-labelledby="log" className="rounded-[14px] border border-rule bg-sheet px-6 py-5">
      <h2 id="log" className="m-0 font-serif text-[22px] font-semibold">
        {t('steps')}
      </h2>
      <ol className="m-0 mt-3 list-none p-0 text-[15px]">
        {(view.events as Ev[]).map((e) => (
          <li key={e.n} className="grid grid-cols-[86px_130px_minmax(0,1fr)] gap-3 border-t border-rule py-2">
            <span className="tabular text-ink-faint">{clock(view.started_at, e.at, locale)}</span>
            <span className="font-bold">
              {t.has(`stage.${e.stage}` as 'stage.asked') ? t(`stage.${e.stage}` as 'stage.asked') : e.stage}
            </span>
            <span className="min-w-0 break-words">
              {e.text}
              {e.url ? (
                <a href={e.url} target="_blank" rel="noreferrer" className="ms-1.5 text-[13.5px]">
                  {new URL(e.url).hostname}
                </a>
              ) : null}
            </span>
          </li>
        ))}
      </ol>
      {kept.length || dropped.length || aside.length ? (
        <div className="mt-4 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr))]">
          {dropped.map((d) => (
            <div key={d.id} className="rounded-xl border-[1.5px] border-debit bg-debit-tint px-3.5 py-2.5">
              <p className="m-0 text-[13px] font-bold text-debit">{t('dropped')}</p>
              <p className="m-0 font-serif text-[15px] line-through decoration-debit/60">“{d.quote}”</p>
            </div>
          ))}
          {aside.map((u) => (
            <div key={u} className="rounded-xl border-[1.5px] border-dotted border-blank bg-blank-tint px-3.5 py-2.5">
              <p className="m-0 text-[13px] font-bold text-blank">{t('setAside')}</p>
              <p className="m-0 break-all text-[14px]">{u}</p>
            </div>
          ))}
          {kept.slice(0, 6).map((k) => (
            <div key={`${k.id}-${k.by}`} className="rounded-xl border border-rule bg-paper px-3.5 py-2.5">
              <p className="m-0 text-[13px] font-bold text-ledger">
                {t('kept')} · {k.by === 'A' ? t('researcherA') : t('researcherB')}
              </p>
              <p className="m-0 font-serif text-[15px]">“{k.quote}”</p>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export function ChallengeRunScreen({ runId }: { runId: string }) {
  const t = useTranslations('challenge');
  const locale = useLocale();
  const qc = useQueryClient();
  // Each step arrives as it happens, with no replay delay (a finished question opens complete at once), and a new
  // step refreshes the view; the view is also asked for every few seconds while the research runs.
  const { events: live } = useEventStream<Ev>(
    `/v1/challenges/${encodeURIComponent(runId)}/events?lang=${encodeURIComponent(locale)}`,
    'step',
  );
  const q = useChallenge(runId);
  useEffect(() => {
    if (!live.length) return;
    const timer = setTimeout(() => qc.invalidateQueries({ queryKey: ['challenge', runId] }), 400);
    return () => clearTimeout(timer);
  }, [live.length, qc, runId]);
  // everything is shown from the start, for a question just asked and for one opened again; it can be folded away
  const [details, setDetails] = useState(true);
  const [checks, setChecks] = useState(false);
  const view = q.data;
  const seen = useMemo(() => new Set([...((view?.events ?? []) as Ev[]), ...live].map((e) => e.stage)), [live, view]);
  if (q.isError) {
    return (
      <div className="min-h-screen bg-paper">
        <TopBar finished={false} running={false} />
        <div className="mx-auto max-w-xl p-8">
          {q.error instanceof ApiError && (q.error.status === 404 || q.error.status === 403) ? (
            <div className="flex flex-col gap-3 rounded-[14px] border border-rule bg-sheet p-5">
              <p className="m-0 text-[18px] font-bold">{t('missingTitle')}</p>
              <p className="m-0 text-ink-soft">{t('missingNote')}</p>
              <ButtonLink href="/challenge" className="self-start">
                {t('askOwn')}
              </ButtonLink>
            </div>
          ) : (
            <ErrorNote error={q.error} retry={() => q.refetch()} />
          )}
        </div>
      </div>
    );
  }
  const finished = !!view?.finished || seen.has('finished');
  const need = (view?.need ?? {}) as {
    country?: string;
    question?: string;
    lang?: string;
    situation?: string;
    asked_by?: string | null;
    title?: string | null;
    translation?: string | null;
  };
  const translation = need.translation && (need.lang ?? 'en').split('-')[0] !== locale ? need.translation : null;
  // the situation is written in English; in another UI language the country alone says where
  const meta = [countryName(need.country ?? '', locale), locale === 'en' ? need.situation : null].filter(Boolean).join('. ');
  const gaps = [...((view?.researcher_a as { gaps?: string[] })?.gaps ?? []), ...((view?.researcher_b as { gaps?: string[] })?.gaps ?? [])];
  return (
    <div className="min-h-screen bg-paper">
      <TopBar finished={finished} running={!finished} />
      <main id="main" className="mx-auto flex max-w-[1320px] flex-col gap-6 px-4 pb-14 pt-7 sm:px-8">
        {!view ? (
          <Skeleton className="h-60" />
        ) : (
          <>
            <section className="grid items-start gap-6 [grid-template-columns:repeat(auto-fit,minmax(min(420px,100%),1fr))]">
              <div>
                {need.asked_by ? <p className="m-0 text-[14px] text-ink-faint">{need.asked_by}</p> : null}
                {need.title ? (
                  <h1 lang={need.lang} className="m-0 mt-0.5 font-serif text-[26px] font-semibold leading-snug">
                    {need.title}
                  </h1>
                ) : null}
                <p className="m-0 mt-1 text-[15px] text-ink-faint">{meta}</p>
                <blockquote
                  lang={need.lang}
                  dir="auto"
                  className="m-0 mt-2.5 font-serif text-[22px] font-medium leading-[1.4] sm:text-[26px]"
                >
                  {need.question}
                </blockquote>
                {translation ? <p className="m-0 mt-2 text-ink-soft">{translation}</p> : null}
                <button
                  type="button"
                  aria-expanded={details}
                  aria-controls="research-details"
                  onClick={() => setDetails((v) => !v)}
                  className="tap mt-3 text-[15px] font-bold text-ledger underline-offset-4 hover:underline"
                >
                  {details ? t('hideDetails') : t('showAll')}
                </button>
              </div>
              <FirstAnswer view={view} seen={seen.has('first_answer')} planSeen={seen.has('plan') || seen.has('compiler')} />
            </section>

            {details ? (
              <div id="research-details" className="flex flex-col gap-6">
                <Checks view={view} seen={seen} />

                {seen.has('plan') || seen.has('compiler') || seen.has('letter') ? (
                  <div className="flex flex-wrap items-start gap-6">
                    <PlanCard view={view} cites={view.cites} />
                    {seen.has('letter') ? <LetterCard view={view} checks={checks} onChecks={() => setChecks((v) => !v)} /> : null}
                  </div>
                ) : (
                  <div className="flex min-h-32 items-center justify-center rounded-[14px] border border-dashed border-rule-strong bg-sheet">
                    <Spinner label={t('compiling')} />
                  </div>
                )}

                {!finished ? (
                  <section className="flex items-start gap-3.5 rounded-[14px] border-[1.5px] border-dotted border-blank bg-sheet px-5 py-4">
                    <span aria-hidden="true" className="mt-1.5 h-3 w-3 shrink-0 animate-pulse-soft rounded-full bg-blank" />
                    <div>
                      <h2 className="m-0 text-[16px] font-bold">{t('running')}</h2>
                      <p className="m-0 mt-1 text-[15px] text-ink-soft">{t('stillRunningText')}</p>
                    </div>
                  </section>
                ) : gaps.length ? (
                  // a finished question searches no more: what it could not settle is said, not promised for later
                  <section aria-labelledby="gaps" className="rounded-[14px] border-[1.5px] border-dotted border-blank bg-sheet px-5 py-4">
                    <h2 id="gaps" className="m-0 text-[16px] font-bold">
                      {t('gapsTitle')}
                    </h2>
                    <ul className="m-0 mt-2 list-disc ps-5 text-[15px]">
                      {[...new Set(gaps)].map((g) => (
                        <li key={g}>{g}</li>
                      ))}
                    </ul>
                    <p className="m-0 mt-2 text-[15px] text-ink-soft">{t('gapsNote')}</p>
                  </section>
                ) : null}

                {checks ? <EventLog view={view} /> : null}
              </div>
            ) : null}
          </>
        )}
      </main>
    </div>
  );
}

export function ChallengeStartScreen() {
  const t = useTranslations('challenge');
  const tm = useTranslations('main');
  return (
    <div className="min-h-screen bg-paper">
      <TopBar finished={false} running={false} />
      <main id="main" className="mx-auto max-w-[1100px] px-4 py-8 sm:px-8">
        <section className="rounded-[18px] bg-night p-6 text-[#F2F5F3] sm:p-12">
          <h1 className="m-0 font-serif text-[32px] font-semibold leading-[1.12] sm:text-[40px]">{t('formTitle')}</h1>
          <p className="m-0 mt-3.5 max-w-[44em] text-[18px] text-night-soft">{t('formLede')}</p>
          <ChallengeForm />
        </section>
        <p className="mt-6 text-[15px] text-ink-soft">{tm('noAccount')}</p>
      </main>
    </div>
  );
}
