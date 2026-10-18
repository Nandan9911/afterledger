'use client';
/** Activity: everything the agents and the family did on the case, newest first, updated live. */
import type { Activity, UsageView } from '@afterledger/contracts';
import { useLocale, useTranslations } from 'next-intl';
import { ErrorNote, h1Class, Sheet, Skeleton } from '@/components/ui';
import { asDate, cls, count, intl, usd, weekdayDate } from '@/lib/format';
import { useActivity } from '@/lib/queries';

const KIND_LOOK: Record<string, string> = {
  decision: 'bg-ledger',
  done: 'bg-ledger',
  check: 'bg-ledger-deep',
  question: 'bg-pencil',
  warning: 'bg-debit',
  research: 'bg-blank',
  step: 'bg-rule-strong',
  info: 'bg-rule-strong',
};

function time(iso: string, locale: string) {
  const d = asDate(iso);
  return d ? new Intl.DateTimeFormat(intl(locale), { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).format(d) : '';
}

/** The model use a step's activity line carries: shown when a live (or replayed) model used tokens on it. */
function usageOf(a: Activity): UsageView | null {
  const u = (a.detail as Record<string, unknown> | undefined)?.usage as UsageView | undefined;
  return u && (u.tokens_in || u.tokens_out) ? u : null;
}

const tokens = (u: UsageView) => u.tokens_in + u.tokens_out + (u.tokens_thinking ?? 0);

export function ActivityScreen({ caseId }: { caseId: string }) {
  const t = useTranslations('activity');
  const locale = useLocale();
  const q = useActivity(caseId, 400);
  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  const items = q.data?.items ?? [];
  const total = q.data?.usage && (q.data.usage.tokens_in || q.data.usage.tokens_out) ? q.data.usage : null;
  const days: Array<[string, Activity[]]> = [];
  for (const a of items) {
    const day = a.at.slice(0, 10);
    const last = days[days.length - 1];
    if (last && last[0] === day) last[1].push(a);
    else days.push([day, [a]]);
  }
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className={h1Class}>{t('title')}</h1>
        <p className="m-0 mt-2 text-ink-soft">{t('lede')}</p>
        {total ? (
          <p className="m-0 mt-1.5 text-[14px] text-ink-faint">
            {t('usageTotal', { calls: total.calls, tokens: count(tokens(total), locale), cost: usd(total.cost_usd, locale) })}
          </p>
        ) : null}
      </header>
      {!q.data ? (
        <Skeleton className="h-96" />
      ) : !items.length ? (
        <Sheet className="px-6 py-5">
          <p className="m-0 text-ink-soft">{t('empty')}</p>
        </Sheet>
      ) : (
        days.map(([day, list]) => (
          <Sheet key={day} as="section" className="px-6 py-4" aria-labelledby={`d-${day}`}>
            <h2 id={`d-${day}`} className="m-0 font-serif text-[20px] font-semibold">
              {weekdayDate(day, locale)}
            </h2>
            <ol className="m-0 mt-2 list-none p-0">
              {list.map((a) => (
                <li key={a.id} className="grid grid-cols-[52px_14px_minmax(0,1fr)] items-baseline gap-2.5 border-t border-rule py-2.5">
                  <span className="text-[14px] tabular text-ink-faint">{time(a.at, locale)}</span>
                  <span aria-hidden="true" className={cls('h-2.5 w-2.5 rounded-full', KIND_LOOK[a.kind] ?? 'bg-rule-strong')} />
                  <div className="min-w-0">
                    <p className="m-0">{a.text}</p>
                    <p className="m-0 text-[13px] text-ink-faint">
                      {t.has(`modules.${a.module}` as 'modules.intake') ? t(`modules.${a.module}` as 'modules.intake') : a.module}
                      {a.agent ? ` · ${a.agent.replaceAll('_', ' ')}` : ''}
                      {(() => {
                        const u = usageOf(a);
                        return u
                          ? ` · ${t('usage', { calls: u.calls, tokens: count(tokens(u), locale), cost: usd(u.cost_usd, locale) })}`
                          : '';
                      })()}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </Sheet>
        ))
      )}
    </div>
  );
}
