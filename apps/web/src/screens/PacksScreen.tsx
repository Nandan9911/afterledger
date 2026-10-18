'use client';
/** Packs: the papers for each claim, with where each pack stands. */
import type { PackRow } from '@afterledger/contracts';
import { FileCheck2 } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useCase } from '@/components/CaseShell';
import { ErrorNote, h1Class, Sheet, Skeleton } from '@/components/ui';
import { Link } from '@/i18n/navigation';
import { cls, dayMonth } from '@/lib/format';
import { usePacks } from '@/lib/queries';

export const PACK_STATUS_LOOK: Record<string, string> = {
  draft: 'border-[1.5px] border-dotted border-blank text-blank bg-sheet',
  ready: 'border-[1.5px] border-dashed border-pencil text-pencil bg-pencil-tint',
  approved: 'border-[1.5px] border-solid border-ledger text-ledger bg-ledger-tint',
  submitted: 'border-[1.5px] border-solid border-ledger text-white bg-ledger',
};

function Card({ r }: { r: PackRow }) {
  const t = useTranslations('packs');
  const locale = useLocale();
  const { caseId } = useCase();
  const p = r.pack;
  const forms = p.items.filter((i) => i.kind !== 'instructions').length;
  const missing = p.enclosures.filter((e) => e.status !== 'have').length;
  const fails = p.checks.filter((c) => c.result === 'fail').length;
  return (
    <li>
      <Link
        href={`/cases/${caseId}/packs/${p.id}`}
        className="block h-full rounded-[14px] border border-rule bg-sheet px-5 py-4 text-ink no-underline hover:border-ink hover:text-ink"
      >
        <div className="flex items-start justify-between gap-3">
          <FileCheck2 size={22} className="mt-0.5 shrink-0 text-ledger" aria-hidden="true" />
          <span className={cls('rounded-full px-2.5 py-0.5 text-[13px] font-bold', PACK_STATUS_LOOK[p.status])}>
            {t(`status.${p.status}` as 'status.ready')}
          </span>
        </div>
        <p className="m-0 mt-3 text-[18px] font-bold leading-snug">{p.title}</p>
        <p className="m-0 mt-1 text-[15px] text-ink-soft">{t('counts', { forms, missing })}</p>
        {p.approved_on ? (
          <p className="m-0 mt-1 text-[14px] text-ledger">{t('approvedOn', { date: dayMonth(p.approved_on, locale) })}</p>
        ) : null}
        {fails ? <p className="m-0 mt-1 text-[14px] font-bold text-debit">{t('checksFail', { count: fails })}</p> : null}
      </Link>
    </li>
  );
}

export function PacksScreen({ caseId }: { caseId: string }) {
  const t = useTranslations('packs');
  const q = usePacks(caseId);
  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  const rows = q.data?.rows ?? [];
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className={h1Class}>{t('title')}</h1>
        <p className="m-0 mt-2 max-w-[46em] text-ink-soft">{t('lede')}</p>
      </header>
      {!q.data ? (
        <Skeleton className="h-64" />
      ) : rows.length ? (
        <ul className="m-0 grid list-none gap-4 p-0 [grid-template-columns:repeat(auto-fill,minmax(min(300px,100%),1fr))]">
          {rows.map((r) => (
            <Card key={r.pack.id} r={r} />
          ))}
        </ul>
      ) : (
        <Sheet className="px-6 py-6">
          <p className="m-0 text-ink-soft">{t('empty')}</p>
        </Sheet>
      )}
    </div>
  );
}
