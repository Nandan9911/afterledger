'use client';
/** Plans: one line per claim, procedure and debt, with its institution, amount, status and how well checked. */
import type { PlanRow } from '@afterledger/contracts';
import { useLocale, useTranslations } from 'next-intl';
import { useCase } from '@/components/CaseShell';
import { ButtonLink, Chip, ErrorNote, h1Class, h2Class, Sheet, Skeleton, Stamp } from '@/components/ui';
import { Link } from '@/i18n/navigation';
import { cls, localText } from '@/lib/format';
import { usePlans } from '@/lib/queries';

export function levelChip(level: string | undefined | null): {
  tone: 'ledger' | 'pencil' | 'blank';
  key: 'gold' | 'verified' | 'one_source' | 'unverified';
} {
  if (level === 'gold') return { tone: 'ledger', key: 'gold' };
  if (level === 'dual_verified' || level === 'adjudicated' || level === 'outcome_confirmed') return { tone: 'ledger', key: 'verified' };
  if (level === 'sourced') return { tone: 'pencil', key: 'one_source' };
  return { tone: 'blank', key: 'unverified' };
}

type State = 'planning' | 'answers' | 'approval' | 'preparing' | 'settledDebt' | PlanRow['claim']['status'];

/** Where a claim stands, in the words of the page's language: while it is planned, what it waits for. */
export function claimState(r: PlanRow): State {
  const status = r.claim.status;
  if (status === 'planning') {
    if (!r.plan) return 'planning';
    if (r.questions) return 'answers';
    return r.plan.approved_by ? 'preparing' : 'approval';
  }
  if (status === 'settled' && r.claim.kind === 'debt') return 'settledDebt';
  return status;
}

function Row({ r }: { r: PlanRow }) {
  const t = useTranslations('plans');
  const locale = useLocale();
  const { caseId } = useCase();
  const plan = r.plan;
  const lv = levelChip(plan?.level);
  const summary = plan ? localText(plan.summary, plan.summary_local, plan.lang_local, locale) : '';
  const settled = r.claim.status === 'settled';
  const tt = useTranslations('today');
  const lateDays = r.claim.clocks.find((c) => c.status === 'late' && c.owner === 'institution')?.days_late ?? 0;
  const state = t(`state.${claimState(r)}` as 'state.planning');
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-5 gap-y-1 border-t border-rule px-5 py-4">
      <div className="min-w-0">
        <p className="m-0 text-[18px] font-bold leading-snug">
          {plan ? (
            <Link href={`/cases/${caseId}/plans/${plan.id}`} className="tap text-ink no-underline hover:text-ledger hover:underline">
              {r.claim.title}
            </Link>
          ) : (
            r.claim.title
          )}
        </p>
        {summary ? <p className="m-0 mt-1 text-[15px] text-ink-soft">{summary}</p> : null}
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[14px]">
          {plan?.plan_key?.startsWith('XX/') ? (
            <Chip tone="blank">
              {(r.claim.research as { status?: string } | undefined)?.status === 'running' ? t('researching') : t('forNow')}
            </Chip>
          ) : (
            <Chip tone={lv.tone}>{t(`level.${lv.key}`)}</Chip>
          )}
          {r.questions ? <Chip tone="pencil">{t('questions', { count: r.questions })}</Chip> : null}
          {plan?.approved_by ? <Chip>{t('approved')}</Chip> : null}
        </div>
      </div>
      <div className="double-rule flex flex-col items-end gap-1 ps-4 text-end">
        {r.amount ? (
          <p className={cls('m-0 font-bold tabular', settled && 'text-ledger')}>{r.amount.text}</p>
        ) : r.claim.status === 'closed' ? (
          <p className="m-0 text-ink-faint">—</p>
        ) : (
          <p className="m-0 text-ink-soft">{t('noAmount')}</p>
        )}
        {r.late ? (
          <Stamp>{lateDays ? tt('lateDays', { count: lateDays }) : state}</Stamp>
        ) : (
          <span className={cls('text-[14px]', settled ? 'font-bold text-ledger' : 'text-ink-soft')}>{state}</span>
        )}
      </div>
    </li>
  );
}

export function PlansScreen({ caseId }: { caseId: string }) {
  const t = useTranslations('plans');
  const q = usePlans(caseId);
  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  const rows = q.data?.rows ?? [];
  const groups: Array<[string, PlanRow[]]> = [
    ['claims', rows.filter((r) => r.claim.kind === 'claim' && r.claim.status !== 'closed')],
    ['procedures', rows.filter((r) => r.claim.kind === 'procedure' && r.claim.status !== 'closed')],
    ['debts', rows.filter((r) => r.claim.kind === 'debt' && r.claim.status !== 'closed')],
    ['closedGroup', rows.filter((r) => r.claim.status === 'closed')],
  ];
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className={h1Class}>{t('title')}</h1>
        <p className="m-0 mt-2 max-w-[46em] text-ink-soft">{t('lede')}</p>
      </header>
      {!q.data ? (
        <Skeleton className="h-96" />
      ) : !rows.length ? (
        <Sheet className="flex flex-col gap-3 px-6 py-6">
          <p className="m-0 text-ink-soft">{t('empty')}</p>
          <p className="m-0 text-[15px] text-ink-soft">{t('emptyHow')}</p>
          <div className="flex flex-wrap gap-2.5">
            <ButtonLink href={`/cases/${caseId}/papers`} size="sm">
              {t('addPapers')}
            </ButtonLink>
            <ButtonLink href={`/cases/${caseId}/leads`} size="sm" variant="secondary">
              {t('openLeads')}
            </ButtonLink>
          </div>
        </Sheet>
      ) : (
        groups
          .filter(([, list]) => list.length)
          .map(([key, list]) => (
            <Sheet key={key} as="section" className="overflow-hidden" aria-labelledby={`g-${key}`}>
              <div className="border-b-2 border-ink px-5 pb-3 pt-4">
                <h2 id={`g-${key}`} className={h2Class}>
                  {t(`groups.${key}` as 'groups.claims')}
                </h2>
                <p className="m-0 mt-0.5 text-[15px] text-ink-soft">{t(`groupsLede.${key}` as 'groupsLede.claims')}</p>
              </div>
              <ul className="m-0 list-none p-0 [&>li:first-child]:border-t-0">
                {list.map((r) => (
                  <Row key={r.claim.id} r={r} />
                ))}
              </ul>
            </Sheet>
          ))
      )}
    </div>
  );
}
