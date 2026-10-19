'use client';
/**
 * One claim's plan, in plain words: each step with its status and its source a tap away, what the institution
 * may not ask for, the institution's clock and what its delay costs (worked out by code), the questions that
 * decide the route (checkpoint C5), and the family's approval. On a phone in Japanese, dates carry the era.
 */
import type { ClockState, Compensation, PlanView } from '@afterledger/contracts';
import { Check, Volume2, X } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { useCase } from '@/components/CaseShell';
import { Button, ButtonLink, Chip, ErrorNote, h2Class, Sheet, Skeleton } from '@/components/ui';
import { useSpeech } from '@/components/Voice';
import { CiteCard, Why } from '@/components/Why';
import { Link, useRouter } from '@/i18n/navigation';
import {
  asDate,
  cls,
  countryName,
  dayMonth,
  eraDate,
  inLanguage,
  institutionLabel,
  languageName,
  listOf,
  localPart,
  longDate,
  personName,
  sentence,
} from '@/lib/format';
import { useAnswer, usePlan } from '@/lib/queries';
import { levelChip } from './PlansScreen';

type Status = 'done' | 'active' | 'blocked' | 'skipped' | 'late' | 'todo' | null;
type Item = {
  key: string;
  text: string;
  textLocal: string;
  status: Status;
  cites: string[];
  note: string;
  stepId: string | null;
};

function itemsOf(view: PlanView): Item[] {
  const p = view.plan;
  const byId = new Map(p.steps.map((s) => [s.id, s]));
  if (p.explained.length) {
    return p.explained.map((raw, i) => {
      const e = raw as {
        text?: string;
        text_local?: string;
        step_id?: string | null;
        cites?: string[];
      };
      const s = e.step_id ? byId.get(e.step_id) : undefined;
      return {
        key: `e${i}`,
        text: e.text ?? '',
        textLocal: e.text_local ?? '',
        status: (s?.status as Status) ?? null,
        cites: e.cites?.length ? e.cites : (s?.cites ?? []),
        note: s?.note ?? '',
        stepId: e.step_id ?? null,
      };
    });
  }
  return p.steps.map((s) => ({
    key: s.id,
    text: s.text,
    textLocal: '',
    status: s.status as Status,
    cites: s.cites,
    note: s.note,
    stepId: s.id,
  }));
}

function useWide() {
  const [wide, setWide] = useState(true);
  useEffect(() => {
    const m = window.matchMedia('(min-width: 1024px)');
    const on = () => setWide(m.matches);
    on();
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, []);
  return wide;
}

function StatusMark({ status, n }: { status: Status; n: number }) {
  const t = useTranslations('plan');
  const label = status ? t(`stepStatus.${status}`) : undefined;
  const base = 'grid h-[26px] w-[26px] place-items-center rounded-full text-[14px] font-bold';
  if (status === 'done')
    return (
      <span role="img" aria-label={label} className={cls(base, 'bg-ledger text-white')}>
        <Check size={14} strokeWidth={3} aria-hidden="true" />
      </span>
    );
  if (status === 'late')
    return (
      <span role="img" aria-label={label} className={cls(base, 'border-2 border-debit bg-debit-tint text-debit')}>
        !
      </span>
    );
  if (status === 'active')
    return (
      <span role="img" aria-label={label} className={cls(base, 'border-2 border-ink')}>
        {n}
      </span>
    );
  if (status === 'blocked')
    return (
      <span role="img" aria-label={label} className={cls(base, 'border-2 border-dotted border-blank text-blank')}>
        {n}
      </span>
    );
  return (
    <span
      role="img"
      aria-label={label}
      className={cls(base, 'border-2 border-rule-strong text-ink-faint', status === 'skipped' && 'opacity-60')}
    >
      {n}
    </span>
  );
}

function lateClockFor(view: PlanView, cites: string[]): ClockState | undefined {
  return view.claim.clocks.find(
    (c) => c.status === 'late' && c.owner === 'institution' && (c.cites.some((x) => cites.includes(x)) || cites.length === 0),
  );
}

function compFor(view: PlanView, item: Item | undefined): Compensation | undefined {
  if (!item) return undefined;
  return (
    view.compensation.find((c) => c.clock.cites.some((x) => item.cites.includes(x))) ??
    (item.stepId === 'chase_late' ? view.compensation[0] : undefined)
  );
}

/** The sources behind "may not ask for": the steps that list what must not be asked, narrowed to the rules about it. */
function mustNotCites(view: PlanView): string[] {
  const all = view.plan.steps.filter((s) => s.must_not_be_asked.length).flatMap((s) => s.cites);
  const narrow = all.filter((c) => /surety|succession|no_|must_not|not_ask/.test(c));
  return narrow.length ? narrow : all;
}

function HowWorked({ comp }: { comp: Compensation }) {
  const t = useTranslations('plan');
  const locale = useLocale();
  const days = comp.clock.days_late;
  return (
    <div className="border-t border-rule pt-3.5">
      <h3 className="m-0 text-[16px] font-bold">{t('howWorked')}</h3>
      <dl className="m-0 mt-2.5 grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-2 text-[16px] tabular">
        {comp.base_rate_pct ? (
          <>
            <dt className="text-ink-soft">{t('baseRate', { date: dayMonth(comp.rate_on, locale) })}</dt>
            <dd className="m-0 text-end font-bold">{comp.base_rate_pct}%</dd>
          </>
        ) : null}
        {comp.rate_pct ? (
          <>
            <dt className="text-ink-soft">{t('rateDelay', { spread: comp.spread_pct ?? '' })}</dt>
            <dd className="m-0 text-end font-bold">{t('perYear', { rate: comp.rate_pct })}</dd>
          </>
        ) : null}
        {comp.amount ? (
          <>
            <dt className="text-ink-soft">{t('amountDue')}</dt>
            <dd className="m-0 text-end font-bold">{comp.amount.text}</dd>
          </>
        ) : null}
        {comp.per_day ? (
          <>
            <dt className="text-ink-soft">{t('perDay')}</dt>
            <dd className="m-0 text-end font-bold">{comp.per_day.text}</dd>
          </>
        ) : null}
        {comp.owed ? (
          <>
            <dt className="font-bold text-debit">{t('owedFor', { count: days })}</dt>
            <dd className="m-0 text-end font-bold text-debit">{comp.owed.text}</dd>
          </>
        ) : null}
      </dl>
      {comp.note ? <p className="m-0 mt-2.5 text-[14px] text-ink-faint">{comp.note}</p> : null}
    </div>
  );
}

function WhyPanel({
  view,
  item,
  onClose,
  local,
  setLocal,
}: {
  view: PlanView;
  item: Item | undefined;
  onClose: () => void;
  local: boolean;
  setLocal: (v: boolean) => void;
}) {
  const t = useTranslations('plan');
  const tc = useTranslations('common');
  const locale = useLocale();
  const lang = view.plan.lang_local;
  const cites = (item?.cites ?? []).map((id) => view.cites[id]).filter(Boolean);
  const comp = compFor(view, item);
  return (
    <aside
      aria-labelledby="why-panel"
      className="flex min-w-0 flex-col gap-3.5 rounded-[14px] border-[1.5px] border-ink bg-sheet px-6 py-[22px] lg:sticky lg:top-6"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 id="why-panel" className={h2Class}>
          {item ? t('whyThis') : tc('why')}
        </h2>
        {item ? (
          <button
            type="button"
            aria-label={tc('close')}
            onClick={onClose}
            className="grid h-11 w-11 shrink-0 place-items-center rounded-[10px] border border-rule-strong bg-sheet"
          >
            <X size={16} aria-hidden="true" />
          </button>
        ) : null}
      </div>
      {item ? (
        <>
          <p className="m-0 text-[16px] font-bold">{local && item.textLocal ? item.textLocal : item.text}</p>
          {cites.length ? cites.map((c) => <CiteCard key={c.id} cite={c} />) : <p className="m-0 text-ink-soft">{t('noSource')}</p>}
          {comp ? <HowWorked comp={comp} /> : null}
        </>
      ) : (
        <p className="m-0 text-ink-soft">{t('whyHint')}</p>
      )}
      {lang && lang !== 'en' ? (
        <Button variant="secondary" className="self-start" onClick={() => setLocal(!local)}>
          {local ? t('readInEnglish') : t('readIn', { language: languageName(lang, locale) || lang })}
        </Button>
      ) : null}
    </aside>
  );
}

function ClockBar({ clock, view }: { clock: ClockState; view: PlanView }) {
  const t = useTranslations('plan');
  const locale = useLocale();
  const { header } = useCase();
  const today = header?.today ?? null;
  const start = asDate(clock.started_on);
  const due = asDate(clock.due_on);
  const now = asDate(today);
  const institution = institutionLabel(view.institution, locale);
  if (!start || !due) {
    return (
      <Sheet className="px-6 py-[22px]">
        <h2 className={h2Class}>{clock.owner === 'family' ? t('familyClock') : t('clock', { institution })}</h2>
        <p className="m-0 mt-2 text-ink-soft">
          {sentence(clock.label)} {t('notStarted')}
        </p>
      </Sheet>
    );
  }
  const day = 86_400_000;
  const length = Math.max(1, Math.round((due.getTime() - start.getTime()) / day));
  const late = clock.status === 'late' ? clock.days_late : 0;
  const ended = clock.status === 'met' || clock.status === 'ended';
  const elapsed = now ? Math.max(0, Math.min(length, Math.round((now.getTime() - start.getTime()) / day))) : 0;
  return (
    <Sheet className="px-6 py-[22px]" aria-labelledby={`clock-${clock.id}`}>
      <h2 id={`clock-${clock.id}`} className={h2Class}>
        {clock.owner === 'family' ? t('familyClock') : t('clock', { institution })}
      </h2>
      <p className="m-0 mt-1 text-[15px] text-ink-soft">{clock.label}</p>
      <div aria-hidden="true" className="relative mt-5 flex h-4 overflow-hidden rounded-full">
        <div className="relative rounded-s-full border-[1.5px] border-ledger bg-ledger-tint" style={{ flex: `${length} 1 0` }}>
          {!late && !ended ? (
            <div className="absolute inset-y-0 start-0 bg-ledger/40" style={{ width: `${(elapsed / length) * 100}%` }} />
          ) : null}
        </div>
        {late ? (
          <div
            className="rounded-e-full"
            style={{
              flex: `${late} 1 0`,
              background: 'repeating-linear-gradient(135deg, #A8321F 0 6px, #C9574A 6px 12px)',
            }}
          />
        ) : null}
      </div>
      <div className="mt-2.5 flex justify-between gap-3 text-[15px] tabular">
        <span>
          <strong>{dayMonth(clock.started_on, locale)}</strong>
          <br />
          {clock.owner === 'institution' ? t('received') : t('started')}
        </span>
        <span className={cls(late ? 'text-center' : 'text-end')}>
          <strong>{dayMonth(clock.due_on, locale)}</strong>
          <br />
          {t('dayDue', { n: length })}
        </span>
        {late ? (
          <span className="text-end text-debit">
            <strong>{t('todayMark', { date: dayMonth(today, locale) })}</strong>
            <br />
            {t('late', { count: late })}
          </span>
        ) : null}
      </div>
      {ended && clock.ended_on ? (
        <p className="m-0 mt-2 text-[15px] font-bold text-ledger">{t('metOn', { date: longDate(clock.ended_on, locale) })}</p>
      ) : null}
    </Sheet>
  );
}

function FamilyDeadlines({ view }: { view: PlanView }) {
  const t = useTranslations('plan');
  const locale = useLocale();
  if (!view.family_clocks.length) return null;
  return (
    <>
      {view.family_clocks.map((c) => {
        const short = inLanguage(c.short_local, locale) ? c.short_local : c.short || c.label;
        const what = short.charAt(0).toUpperCase() + short.slice(1);
        return (
          <section key={c.id} className="rounded-[14px] border-[1.5px] border-debit bg-debit-tint px-4 py-3">
            <p className="m-0 text-[14px] font-bold text-debit">{t('importantDeadline')}</p>
            <p className="m-0 mt-0.5 text-[18px] font-bold leading-snug">
              {t('deadlineBy', {
                what,
                date: locale === 'ja' ? eraDate(c.due_on) : longDate(c.due_on, locale),
              })}
            </p>
            <p className="m-0 mt-1 text-[15px]">
              {sentence(c.label)} <Why ids={c.cites} cites={view.cites} title={what} />
            </p>
          </section>
        );
      })}
    </>
  );
}

function Questions({ view }: { view: PlanView }) {
  const t = useTranslations('plan');
  const tt = useTranslations('task');
  const locale = useLocale();
  const { caseId, viewerId, readOnly } = useCase();
  const answer = useAnswer(caseId);
  if (!view.questions.length) return null;
  return (
    <section aria-labelledby="questions" className="rounded-[14px] border-[1.5px] border-dashed border-pencil bg-pencil-tint px-6 py-5">
      <h2 id="questions" className={h2Class}>
        {t('questionsTitle', { count: view.questions.length })}
      </h2>
      {view.questions.map((q) => (
        <div key={q.id} className="mt-3.5 border-t border-pencil/30 pt-3.5 first:border-t-0 first:pt-0">
          <p className="m-0 text-[18px] font-bold">
            {q.question_local && q.language.split('-')[0] === locale ? q.question_local : q.question}
          </p>
          {q.why ? <p className="m-0 mt-1 text-[15px] text-ink-soft">{q.why}</p> : null}
          <div className="mt-2.5 flex flex-wrap gap-2.5">
            {q.options.map((o, i) => (
              <Button
                key={o.value}
                variant={i === 0 ? 'dark' : 'secondary'}
                disabled={answer.isPending || readOnly}
                onClick={() =>
                  answer.mutate({ checkpointId: q.id, answer: o.value, by: viewerId }, { onSuccess: () => toast.success(tt('answered')) })
                }
              >
                {t.has(`options.${o.value}` as 'options.yes') ? t(`options.${o.value}` as 'options.yes') : o.label}
              </Button>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

/** A claim no stored plan covered: its interim plan, and the research that will replace it. */
function ResearchNote({ view }: { view: PlanView }) {
  const t = useTranslations('plan');
  if (!isInterim(view.plan.plan_key)) return null;
  const r = (view.claim.research ?? {}) as { status?: string; run_id?: string };
  const running = r.status === 'running';
  return (
    <section
      role="status"
      className="flex items-start gap-3.5 rounded-[14px] border-[1.5px] border-dotted border-blank bg-sheet px-5 py-4"
    >
      {running ? <span aria-hidden="true" className="mt-1.5 h-3 w-3 shrink-0 animate-pulse-soft rounded-full bg-blank" /> : null}
      <div className="min-w-0">
        <p className="m-0 text-[17px] font-bold">{running ? t('interim.runningTitle') : t('interim.waitingTitle')}</p>
        <p className="m-0 mt-1 text-[15px] text-ink-soft">
          {running ? t('interim.runningNote') : r.status === 'off' ? t('interim.offNote') : t('interim.waitingNote')}
        </p>
        {r.run_id ? (
          <Link href={`/challenge/${r.run_id}`} className="mt-1.5 inline-block text-[15px] font-bold">
            {running ? t('interim.watch') : t('interim.see')}
          </Link>
        ) : null}
      </div>
    </section>
  );
}

export function isInterim(planKey: string | null | undefined): boolean {
  return Boolean(planKey && planKey.startsWith('XX/'));
}

function Approval({ view }: { view: PlanView }) {
  const t = useTranslations('plan');
  const { caseId, viewerId, readOnly } = useCase();
  const answer = useAnswer(caseId);
  const router = useRouter();
  const a = view.approval;
  if (!a || a.status !== 'open' || view.questions.length) return null;
  return (
    <section className="flex flex-wrap items-center justify-between gap-4 rounded-[14px] border-[1.5px] border-ledger bg-ledger-tint px-6 py-5">
      <div>
        <p className="m-0 text-[18px] font-bold">{t('approveAsk')}</p>
        {a.why ? <p className="m-0 mt-1 text-[15px] text-ink-soft">{a.why}</p> : null}
      </div>
      <div className="flex flex-wrap gap-2.5">
        <Button
          size="lg"
          disabled={answer.isPending || readOnly}
          onClick={() =>
            answer.mutate({ checkpointId: a.id, answer: 'approve', by: viewerId }, { onSuccess: () => toast.success(t('approved')) })
          }
        >
          {t('approve')}
        </Button>
        <Button
          size="lg"
          variant="secondary"
          onClick={() => router.push(`/cases/${caseId}/ask?q=${encodeURIComponent(t('askAbout', { title: view.claim.title }))}`)}
        >
          {t('haveQuestion')}
        </Button>
      </div>
    </section>
  );
}

function Documents({ view }: { view: PlanView }) {
  const t = useTranslations('plan');
  if (!view.plan.documents.length) return null;
  const tone: Record<string, string> = {
    have: 'text-ledger font-bold',
    filled: 'text-ledger font-bold',
    signed: 'text-ledger font-bold',
    submitted: 'text-ledger font-bold',
    missing: 'text-pencil font-bold',
    needs_certifying: 'text-pencil font-bold',
    needs_translating: 'text-pencil font-bold',
    unknown: 'text-blank font-bold',
  };
  return (
    <Sheet className="px-6 py-[22px]" aria-labelledby="docs">
      <h2 id="docs" className={h2Class}>
        {t('documents')}
      </h2>
      <ul className="m-0 mt-3 list-none p-0">
        {view.plan.documents.map((d) => (
          <li key={d.id} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-t border-rule py-2.5">
            <div className="min-w-0 flex-1">
              <p className="m-0">{d.name}</p>
              {d.note ? <p className="m-0 text-[14px] text-ink-soft">{d.note}</p> : null}
            </div>
            <span className={cls('text-[15px]', tone[d.status] ?? 'text-ink-soft')}>{t(`docStatus.${d.status}`)}</span>
          </li>
        ))}
      </ul>
    </Sheet>
  );
}

export function PlanScreen({ caseId, planId }: { caseId: string; planId: string }) {
  const t = useTranslations('plan');
  const tc = useTranslations('common');
  const locale = useLocale();
  const { header } = useCase();
  const q = usePlan(caseId, planId);
  const wide = useWide();
  const speech = useSpeech();
  const view = q.data;
  const items = useMemo(() => (view ? itemsOf(view) : []), [view]);
  const [sel, setSel] = useState<string | null | undefined>(undefined);
  const lang = view?.plan.lang_local ?? '';
  const [local, setLocal] = useState<boolean | null>(null);
  const showLocal = local ?? (!!lang && lang.split('-')[0] === locale);

  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  if (!view) {
    return (
      <div className="flex flex-col gap-5">
        <Skeleton className="h-10 w-96" />
        <Skeleton className="h-80" />
      </div>
    );
  }
  const defaultSel = items.find((i) => i.status === 'active' && i.cites.length)?.key ?? items.find((i) => i.status === 'late')?.key ?? null;
  const selected = sel === undefined ? defaultSel : sel;
  const current = items.find((i) => i.key === selected);
  const p = view.plan;
  const lv = levelChip(p.level);
  const summary = showLocal && p.summary_local ? p.summary_local : p.summary;
  const institution = institutionLabel(view.institution, locale);
  const deceased = header?.deceased;
  const instClocks = view.claim.clocks.filter(
    (c) => c.owner === 'institution' && c.kind === 'deadline' && (c.started_on || c.status !== 'not_started'),
  );
  const goldCountry = countryName(p.jurisdiction, locale);

  return (
    <div className="flex flex-wrap items-start gap-7">
      <div className="flex min-w-0 flex-[3_1_520px] flex-col gap-6">
        <header>
          {locale === 'ja' && deceased?.death_date ? (
            <p className="m-0 text-[13px] text-ink-faint">
              故 {deceased.display_name} 様　{eraDate(deceased.death_date)} ご逝去
            </p>
          ) : null}
          <p className="m-0 text-[15px]">
            <Link href={`/cases/${caseId}/plans`} className="tap font-bold">
              {t('crumb')}
            </Link>
            <span className="text-ink-faint"> / {institution || view.claim.title}</span>
          </p>
          <h1 className="m-0 mt-1.5 font-serif text-[28px] font-semibold leading-tight sm:text-[34px]">
            {localPart(p.title, locale) ?? view.claim.title}
          </h1>
          {summary ? <p className="m-0 mt-2 text-ink-soft">{summary}</p> : null}
          <div className="mt-3 flex flex-wrap gap-2 text-[14px]">
            <Chip tone={lv.tone}>
              {p.unverified
                ? t('verifiedLabel.unverified')
                : t.has(`verifiedLabel.${p.level}` as 'verifiedLabel.gold')
                  ? t(`verifiedLabel.${p.level}` as 'verifiedLabel.gold')
                  : p.verified_label || t(`level.${lv.key}`)}
            </Chip>
            {p.gold_match ? <Chip>{t('gold', { country: goldCountry })}</Chip> : null}
            {view.approved_by && p.approved_on ? (
              <Chip>
                {t('approvedBy', {
                  name: personName(view.approved_by, locale),
                  date: dayMonth(p.approved_on, locale),
                })}
              </Chip>
            ) : view.approval ? (
              <Chip tone="pencil">{t('notApproved')}</Chip>
            ) : null}
            {p.research_label && !isInterim(p.plan_key) ? (
              <Chip>
                {p.verified_on
                  ? t(p.unverified || p.level === 'draft' ? 'researchedOn' : 'verifiedOn', { date: longDate(p.verified_on, locale) })
                  : t('researchedNow')}
              </Chip>
            ) : null}
          </div>
        </header>

        <ResearchNote view={view} />
        <FamilyDeadlines view={view} />
        <Questions view={view} />
        <Approval view={view} />

        {view.procedures.length ? (
          <p className="m-0 rounded-[10px] bg-blank-tint px-4 py-3 text-[16px]">
            {t('needs', {
              list: listOf(
                view.procedures.map((x) => localPart(x, locale) ?? x),
                locale,
              ),
            })}
          </p>
        ) : null}
        {view.needed_by.length ? (
          <p className="m-0 rounded-[10px] bg-blank-tint px-4 py-3 text-[16px]">
            {t('neededBy', {
              list: listOf(
                view.needed_by.map((x) => localPart(x, locale) ?? x),
                locale,
              ),
            })}
          </p>
        ) : null}

        <Sheet className="px-6 py-[22px]" aria-labelledby="steps">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 id="steps" className="m-0 font-serif text-[24px] font-semibold">
              {t('steps')}
            </h2>
            <button
              type="button"
              onClick={() =>
                speech.speak(
                  items.map((i, n) => `${n + 1}. ${showLocal && i.textLocal ? i.textLocal : i.text}`).join(' '),
                  showLocal && lang ? lang.split('-')[0] : 'en',
                )
              }
              className="inline-flex min-h-10 items-center gap-1.5 rounded-[10px] border border-rule-strong bg-sheet px-3 text-[15px] font-bold"
            >
              <Volume2 size={16} aria-hidden="true" />
              {speech.speaking ? tc('stop') : tc('listen')}
            </button>
          </div>
          <ol className="m-0 mt-3.5 list-none p-0">
            {items.map((it, i) => {
              const lateClock = it.status === 'late' ? lateClockFor(view, it.cites) : undefined;
              const on = it.key === selected;
              return (
                <li
                  key={it.key}
                  className={cls(
                    'grid grid-cols-[32px_minmax(0,1fr)_auto] items-start gap-3 border-t border-rule py-3',
                    it.status === 'active' && '-mx-3 rounded-lg bg-[#FFF8D6] px-3',
                    it.status === 'skipped' && 'text-ink-faint line-through',
                  )}
                >
                  <StatusMark status={it.status} n={i + 1} />
                  <div className="min-w-0">
                    <p className="m-0" lang={showLocal && it.textLocal ? lang : undefined}>
                      {showLocal && it.textLocal ? it.textLocal : it.text}
                    </p>
                    {lateClock ? (
                      <p className="m-0 mt-1 text-[15px] font-bold text-debit">
                        {t('dueOn', {
                          date: dayMonth(lateClock.due_on, locale),
                        })}{' '}
                        {t('late', { count: lateClock.days_late })}
                      </p>
                    ) : it.status === 'blocked' ? (
                      <p className="m-0 mt-1 text-[15px] text-blank">{t('waitingFor')}</p>
                    ) : null}
                  </div>
                  {it.cites.length ? (
                    wide ? (
                      <button
                        type="button"
                        aria-pressed={on}
                        onClick={() => setSel(on ? null : it.key)}
                        className={cls('tap text-[15px] font-bold text-ledger underline-offset-4 hover:underline', on && 'underline')}
                      >
                        {tc('why')}
                      </button>
                    ) : (
                      <Why ids={it.cites} cites={view.cites} title={showLocal && it.textLocal ? it.textLocal : it.text} />
                    )
                  ) : (
                    <span />
                  )}
                </li>
              );
            })}
          </ol>
          {p.must_not_be_asked.length ? (
            <p className="m-0 mt-4 rounded-[10px] bg-paper px-3.5 py-3 text-[16px]">
              <strong>
                {t('mustNot', {
                  institution: institution || t('theInstitution'),
                })}
              </strong>{' '}
              {p.must_not_be_asked.map((m) => m.charAt(0).toLowerCase() + m.slice(1)).join(t('or'))}. {t('mustNotTail')}{' '}
              <Why ids={mustNotCites(view)} cites={view.cites} title={t('mustNot', { institution })} />
            </p>
          ) : null}
          {p.contested.length ? (
            <p className="m-0 mt-3 rounded-[10px] border-[1.5px] border-dashed border-pencil bg-pencil-tint px-3.5 py-3 text-[16px]">
              {t('contested')}
            </p>
          ) : null}
          {p.level === 'draft' && !isInterim(p.plan_key) ? (
            <p className="m-0 mt-3 rounded-[10px] bg-blank-tint px-3.5 py-3 text-[16px]">{t('unverified')}</p>
          ) : null}
        </Sheet>

        {instClocks.map((c) => (
          <ClockBar key={c.id} clock={c} view={view} />
        ))}
        <Documents view={view} />
        {view.pack_id ? (
          <ButtonLink href={`/cases/${caseId}/packs/${view.pack_id}`} className="self-start">
            {t('openPack')}
          </ButtonLink>
        ) : view.approved_by && view.claim.kind !== 'procedure' ? (
          // approved: the papers are prepared in the background, and appear here by themselves
          <p role="status" className="m-0 flex items-center gap-2.5 text-[16px] text-ink-soft">
            <span aria-hidden="true" className="h-2.5 w-2.5 shrink-0 animate-pulse-soft rounded-full bg-ledger" />
            {t('preparingPack')}
          </p>
        ) : null}
      </div>
      {wide ? (
        <div className="min-w-0 flex-[2_1_360px]">
          <WhyPanel view={view} item={current} onClose={() => setSel(null)} local={showLocal} setLocal={setLocal} />
        </div>
      ) : lang && lang !== 'en' ? (
        <Button variant="secondary" onClick={() => setLocal(!showLocal)}>
          {showLocal ? t('readInEnglish') : t('readIn', { language: languageName(lang, locale) || lang })}
        </Button>
      ) : null}
    </div>
  );
}
