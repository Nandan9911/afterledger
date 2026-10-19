'use client';
import type { CurrencyTotals, TaskView, TodayView } from '@afterledger/contracts';
import { Mic, Volume2 } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useEffect, useRef } from 'react';
import { useCase } from '@/components/CaseShell';
import { TaskActions } from '@/components/TaskActions';
import { Avatar, CertaintyPill, ErrorNote, h1Class, h2BigClass, h2Class, People, Sheet, Skeleton, Stamp } from '@/components/ui';
import { useSpeech } from '@/components/Voice';
import { Link } from '@/i18n/navigation';
import { localeNames, type Locale } from '@/i18n/routing';
import { cls, dayMonth, daysBetween, localText, personFirst, weekdayDate } from '@/lib/format';
import { useRefreshToday, useToday } from '@/lib/queries';

function MoneyCard({ totals, unknown }: { totals: CurrencyTotals; unknown: number }) {
  const t = useTranslations('today');
  const { caseId } = useCase();
  const known = Number(totals.known.amount ?? 0);
  const paid = Number(totals.paid.amount ?? 0);
  const share = known > 0 ? Math.max(0, Math.min(100, (paid / known) * 100)) : 0;
  return (
    <Sheet className="px-6 py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <h2 className="m-0 text-[17px] font-bold">
          {totals.holdings_known ? t('found', { amount: totals.known.short, count: totals.holdings_known }) : t('noneKnown')}
        </h2>
        <Link href={`/cases/${caseId}/money`} className="text-[15px] font-bold">
          {t('openMoney')}
        </Link>
      </div>
      <div aria-hidden="true" className="mt-3.5 flex h-3.5 overflow-hidden rounded-full bg-rule">
        <div className="bg-ledger" style={{ width: `${share}%` }} />
        <div className="striped" style={{ width: `${100 - share}%` }} />
      </div>
      <dl className="mt-3.5 grid gap-x-6 gap-y-3 text-[15px] [grid-template-columns:repeat(auto-fit,minmax(min(190px,100%),1fr))]">
        <div>
          <dt className="text-ink-faint">{t('paid')}</dt>
          <dd className="m-0 mt-0.5 text-[20px] font-bold tabular text-ledger">{totals.paid.short}</dd>
        </div>
        <div>
          <dt className="text-ink-faint">{t('inProgress')}</dt>
          <dd className="m-0 mt-0.5 text-[20px] font-bold tabular">{totals.in_progress.short}</dd>
        </div>
        {unknown ? (
          <div>
            <dt className="text-ink-faint">{t('notKnown')}</dt>
            <dd className="m-0 mt-0.5 text-[20px] font-bold">{t('leadsCount', { count: unknown })}</dd>
          </div>
        ) : null}
        {Number(totals.owed.amount ?? 0) > 0 ? (
          <div>
            <dt className="text-ink-faint">{t('owed')}</dt>
            <dd className="m-0 mt-0.5 text-[20px] font-bold tabular text-debit">{totals.owed.short}</dd>
          </div>
        ) : null}
      </dl>
    </Sheet>
  );
}

function TaskItem({ tv, n, today, locale }: { tv: TaskView; n: number; today: TodayView; locale: string }) {
  const task = tv.task;
  const title = localText(task.title, task.title_local, task.lang_local, locale);
  const body = localText(task.body, task.body_local, task.lang_local, locale);
  return (
    <li className="grid grid-cols-[44px_minmax(0,1fr)] gap-x-3 gap-y-1 border-b border-rule py-5">
      <span className="font-serif text-[28px] font-semibold leading-[1.1] text-ledger">{n}</span>
      <div>
        <h3 className="m-0 text-[20px] font-bold leading-snug">{title}</h3>
        {body ? <p className="m-0 mt-1.5 text-ink-soft">{body}</p> : null}
        <div className="mt-3.5 flex flex-wrap items-center gap-3.5">
          <TaskActions actions={task.actions} cites={today.cites} />
          <span className="ms-auto">
            <People people={tv.owners} />
          </span>
        </div>
      </div>
    </li>
  );
}

function Deadlines({ today, locale }: { today: TodayView; locale: string }) {
  const t = useTranslations('today');
  return (
    <Sheet className="px-[22px] py-5" aria-labelledby="deadlines">
      <h2 id="deadlines" className={h2Class}>
        {t('deadlines')}
      </h2>
      {today.deadlines.length ? (
        <ul className="m-0 mt-3 list-none p-0">
          {today.deadlines.map((d, i) => (
            <li key={i} className="grid grid-cols-[64px_minmax(0,1fr)] gap-3 border-t border-rule py-3">
              <span className={cls('font-bold tabular', d.late && 'text-debit')}>{dayMonth(d.date, locale)}</span>
              <div>
                <p className="m-0 font-bold">{d.title}</p>
                {d.late ? (
                  <Stamp className="mt-1.5">{t('lateDays', { count: daysBetween(d.date, today.date) })}</Stamp>
                ) : d.note ? (
                  <p className="m-0 mt-0.5 text-[15px] text-ink-soft">{d.note}</p>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="m-0 mt-3 text-ink-soft">{t('noDeadlines')}</p>
      )}
    </Sheet>
  );
}

function Unsure({ today }: { today: TodayView }) {
  const t = useTranslations('today');
  const tc = useTranslations('common');
  return (
    <Sheet className="px-[22px] py-5" aria-labelledby="unsure">
      <h2 id="unsure" className={h2Class}>
        {t('unsure')}
      </h2>
      {today.uncertain.length ? (
        <ul className="m-0 mt-3 flex list-none flex-col gap-3.5 p-0">
          {today.uncertain.map((u, i) => (
            <li key={i}>
              <CertaintyPill certainty={u.certainty}>{u.certainty === 'check' ? tc('check') : tc('unknown')}</CertaintyPill>
              <p className="m-0 mt-1.5 text-[16px]">{u.text}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="m-0 mt-3 text-ink-soft">{t('allSure')}</p>
      )}
    </Sheet>
  );
}

/** The phone view of Today: the big ask button, the viewer's own tasks, others' work, and what has come in. */
function PhoneToday({ today, locale, greeting }: { today: TodayView; locale: string; greeting: string }) {
  const t = useTranslations('today');
  const { caseId, header } = useCase();
  const speech = useSpeech();
  const mine = today.tasks.filter((tv) => tv.for_viewer);
  const others = today.tasks.filter((tv) => !tv.for_viewer);
  const totals = today.totals[0];
  const known = Number(totals?.known.amount ?? 0);
  const paid = Number(totals?.paid.amount ?? 0);
  return (
    <div className="flex flex-col gap-3 lg:hidden">
      <div>
        <p className="m-0 text-[15px] text-ink-faint">{header?.case.title}</p>
        <h1 className="m-0 font-serif text-[28px] font-semibold leading-snug">{weekdayDate(today.date, locale)}</h1>
        <p className="m-0 mt-1 text-[17px] text-ink-soft">{greeting}</p>
      </div>
      <Link
        href={`/cases/${caseId}/ask`}
        className="flex w-full items-center gap-3.5 rounded-2xl bg-ledger px-3.5 py-3 text-start text-white no-underline hover:bg-ledger-deep hover:text-white"
      >
        <span aria-hidden="true" className="grid h-[52px] w-[52px] shrink-0 place-items-center rounded-full bg-white/15">
          <Mic size={26} />
        </span>
        <span className="flex flex-col">
          <span className="text-[20px] font-bold">{t('askBig')}</span>
          <span className="text-[15px] opacity-90">{t('askBigSub', { language: localeNames[locale as Locale] ?? locale })}</span>
        </span>
      </Link>
      <h2 className="m-0 mt-1 text-[18px] font-bold">{t('yourTasks', { count: mine.length })}</h2>
      {mine.map((tv) => (
        <Sheet key={tv.task.id} className="px-4 py-3.5">
          <h3 className="m-0 text-[20px] font-bold leading-snug">
            {localText(tv.task.title, tv.task.title_local, tv.task.lang_local, locale)}
          </h3>
          <p className="m-0 mt-1 text-[16px] text-ink-soft">{localText(tv.task.body, tv.task.body_local, tv.task.lang_local, locale)}</p>
          <div className="mt-2.5 flex flex-wrap gap-2.5">
            <TaskActions actions={tv.task.actions.filter((a) => !a.target.startsWith('why:'))} cites={today.cites} />
            <button
              type="button"
              onClick={() =>
                speech.speak(
                  `${localText(tv.task.title, tv.task.title_local, tv.task.lang_local, locale)}. ${localText(tv.task.body, tv.task.body_local, tv.task.lang_local, locale)}`,
                  tv.task.lang_local && tv.task.lang_local === locale ? locale : 'en',
                )
              }
              className="inline-flex min-h-[46px] items-center gap-1.5 rounded-[10px] border border-rule-strong bg-sheet px-4 font-bold"
            >
              <Volume2 size={18} aria-hidden="true" />
              {speech.speaking ? t('stopListen') : t('listen')}
            </button>
          </div>
        </Sheet>
      ))}
      {today.late.map((l) => (
        <section key={l.claim_id} className="flex items-center gap-3 rounded-[14px] border-[1.5px] border-debit bg-debit-tint px-3.5 py-3">
          <Stamp className="shrink-0 -rotate-3">{t('lateDays', { count: l.days_late })}</Stamp>
          <p className="m-0 text-[16px] leading-snug">
            {l.owners.length
              ? t('lateOwes', {
                  name: personFirst(l.owners[0], locale),
                  institution: l.institution,
                  amount: l.compensation?.short ?? '',
                })
              : t('lateOwesNoOwner', { institution: l.institution, amount: l.compensation?.short ?? '' })}
          </p>
        </section>
      ))}
      {others.filter((tv) => !tv.task.claim_id || !today.late.some((l) => l.claim_id === tv.task.claim_id)).length ? (
        <section>
          <h2 className="m-0 text-[16px] font-bold text-ink-faint">{t('othersDoing')}</h2>
          <ul className="m-0 mt-1 flex list-none flex-col gap-2 p-0">
            {others
              .filter((tv) => !tv.task.claim_id || !today.late.some((l) => l.claim_id === tv.task.claim_id))
              .map((tv) => (
                <li key={tv.task.id} className="flex items-center gap-2 text-[16px]">
                  {tv.owners[0] ? <Avatar person={tv.owners[0]} /> : null}
                  <span>{localText(tv.task.title, tv.task.title_local, tv.task.lang_local, locale)}</span>
                </li>
              ))}
          </ul>
        </section>
      ) : null}
      {totals ? (
        <section className="px-1 pt-0.5">
          <p className="m-0 text-[16px] text-ink-soft">{t('receivedSoFar')}</p>
          <p className="m-0 text-[26px] font-bold tabular">
            {totals.paid.short} <span className="text-[16px] font-normal text-ink-soft">{t('ofTotal', { total: totals.known.short })}</span>
          </p>
          <div aria-hidden="true" className="mt-1.5 h-2 overflow-hidden rounded-full bg-rule">
            <div className="h-full bg-ledger" style={{ width: `${known ? (paid / known) * 100 : 0}%` }} />
          </div>
        </section>
      ) : null}
    </div>
  );
}

export function TodayScreen({ caseId }: { caseId: string }) {
  const t = useTranslations('today');
  const locale = useLocale();
  const { viewerId, header, readOnly } = useCase();
  // asked once the case says who is viewing, so the page is not fetched twice (once for nobody, once for them)
  const q = useToday(caseId, viewerId, Boolean(header));
  const refresh = useRefreshToday(caseId);
  // each family member has a greeting of their own; one written for someone else is redone once, quietly
  const redone = useRef<string | null>(null);
  const stale = !!(viewerId && q.data?.greeting_for && q.data.greeting_for !== viewerId);
  const { mutate, isPending } = refresh;
  useEffect(() => {
    if (stale && viewerId && !readOnly && redone.current !== viewerId && !isPending) {
      redone.current = viewerId;
      mutate(viewerId);
    }
  }, [stale, viewerId, readOnly, isPending, mutate]);
  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  const today = q.data;
  if (!today) {
    return (
      <div className="flex flex-col gap-6">
        <Skeleton className="h-12 w-80" />
        <Skeleton className="h-36" />
        <Skeleton className="h-72" />
      </div>
    );
  }
  // someone else's greeting is not shown while this person's own is written (it arrives by itself)
  const greeting = (!stale && localText(today.greeting, today.greeting_local, today.greeting_lang, locale)) || t('greetingFallback');
  return (
    <>
      <PhoneToday today={today} locale={locale} greeting={greeting} />
      <div className="hidden flex-col gap-7 lg:flex">
        <header>
          <h1 className={h1Class}>{weekdayDate(today.date, locale)}</h1>
          <p className="m-0 mt-2 text-[18px] text-ink-soft">{greeting}</p>
          {today.reading.length ? (
            <p className="m-0 mt-1 text-[15px] font-bold text-pencil">{t('reading', { papers: today.reading.join(', ') })}</p>
          ) : null}
        </header>
        {today.totals.map((tot) => (
          <MoneyCard key={tot.currency} totals={tot} unknown={today.totals.length === 1 ? today.unknown_values : 0} />
        ))}
        <div className="grid items-start gap-7 [grid-template-columns:repeat(auto-fit,minmax(min(420px,100%),1fr))]">
          <section aria-labelledby="things">
            <h2 id="things" className={cls(h2BigClass, 'mb-1.5')}>
              {t('things', { count: today.tasks.length })}
            </h2>
            {today.tasks.length ? (
              <ol className="m-0 list-none border-t-2 border-ink p-0">
                {today.tasks.map((tv, i) => (
                  <TaskItem key={tv.task.id} tv={tv} n={i + 1} today={today} locale={locale} />
                ))}
              </ol>
            ) : (
              <p className="m-0 border-t-2 border-ink pt-4 text-ink-soft">{t('nothing')}</p>
            )}
          </section>
          <div className="flex flex-col gap-7">
            <Deadlines today={today} locale={locale} />
            <Unsure today={today} />
          </div>
        </div>
      </div>
    </>
  );
}
