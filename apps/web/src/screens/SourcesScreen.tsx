'use client';
/** Sources: every rule the case relies on, in the source's own words, how well it is checked and how fresh it is. */
import type { SourceRow, SourcesView } from '@afterledger/contracts';
import { ExternalLink } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useMemo, useState } from 'react';
import { ErrorNote, h1Class, Sheet, Skeleton } from '@/components/ui';
import { asDate, cls, dayMonth, intl } from '@/lib/format';
import { useSources } from '@/lib/queries';

const LEVEL_ORDER = ['gold', 'outcome_confirmed', 'adjudicated', 'dual_verified', 'sourced', 'draft'];
const LEVEL_BAR: Record<string, string> = {
  gold: 'bg-ink',
  outcome_confirmed: 'bg-ledger-deep',
  adjudicated: 'bg-ledger-deep',
  dual_verified: 'bg-ledger',
  sourced: 'bg-[#C9A84E]',
  draft: 'bg-blank-tint border-[1.5px] border-dotted border-blank',
};
const LEVEL_CHIP: Record<string, string> = {
  gold: 'bg-ink text-white',
  outcome_confirmed: 'bg-ledger-tint text-ledger border-[1.5px] border-ledger',
  adjudicated: 'bg-ledger-tint text-ledger border-[1.5px] border-ledger',
  dual_verified: 'bg-ledger-tint text-ledger border-[1.5px] border-ledger',
  sourced: 'bg-pencil-tint text-pencil border-[1.5px] border-dashed border-pencil',
  draft: 'text-blank border-[1.5px] border-dotted border-blank',
};

function shortDate(iso: string | null | undefined, locale: string) {
  const d = asDate(iso);
  return d ? new Intl.DateTimeFormat(intl(locale), { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(d) : '';
}

function Levels({ view }: { view: SourcesView }) {
  const t = useTranslations('sources');
  const total = view.counts.reduce((a, c) => a + c.count, 0) + view.researching.length;
  const counts = [...view.counts].sort((a, b) => LEVEL_ORDER.indexOf(a.level) - LEVEL_ORDER.indexOf(b.level));
  return (
    <Sheet className="px-[22px] py-[18px]" aria-labelledby="levels">
      <h2 id="levels" className="m-0 text-[16px] font-bold">
        {t('levels', { count: total })}
      </h2>
      <div aria-hidden="true" className="mt-3 flex h-3.5 gap-px overflow-hidden rounded-full">
        {counts.map((c) => (
          <div key={c.level} className={LEVEL_BAR[c.level] ?? 'bg-rule'} style={{ flex: `${c.count} 1 0` }} />
        ))}
        {view.researching.length ? <div className={LEVEL_BAR.draft} style={{ flex: `${view.researching.length} 1 0` }} /> : null}
      </div>
      <ul className="m-0 mt-3 flex list-none flex-wrap gap-x-6 gap-y-2 p-0 text-[15px]">
        {counts.map((c) => (
          <li key={c.level} className="flex items-center gap-2">
            <span aria-hidden="true" className={cls('h-3 w-3 rounded-[3px]', LEVEL_BAR[c.level])} />
            {t('levelCount', { count: c.count, label: c.label.toLowerCase() })}
          </li>
        ))}
        {view.researching.length ? (
          <li className="flex items-center gap-2">
            <span aria-hidden="true" className={cls('h-3 w-3 rounded-[3px]', LEVEL_BAR.draft)} />
            {t('researchingCount', { count: view.researching.length })}
          </li>
        ) : null}
      </ul>
    </Sheet>
  );
}

function Row({ r, locale }: { r: SourceRow; locale: string }) {
  const t = useTranslations('sources');
  const tc = useTranslations('common');
  const c = r.cite;
  const showTranslation = c.quote_translation && c.language.split('-')[0] !== locale;
  return (
    <tr>
      <td className="border-b border-rule py-3.5 pe-4 ps-5 align-top">
        <p className="m-0 font-bold">{c.text}</p>
        {r.used_by.length ? <p className="m-0 mt-1 text-[13.5px] text-ink-faint">{t('usedBy', { list: r.used_by.join('; ') })}</p> : null}
      </td>
      <td className="border-b border-rule px-4 py-3.5 align-top">
        <p lang={c.language} className="m-0 font-serif">
          “{c.quote}”
        </p>
        {showTranslation ? (
          <p className="m-0 mt-1 text-[14px] text-ink-soft">
            {t('translation')}: {c.quote_translation}
          </p>
        ) : null}
      </td>
      <td className="border-b border-rule px-4 py-3.5 align-top">
        {c.source_url ? (
          <a href={c.source_url} target="_blank" rel="noreferrer" className="inline-flex items-start gap-1 font-bold">
            <span>
              {c.source_title}
              {c.locator ? `, ${c.locator}` : ''}
            </span>
            <ExternalLink size={13} className="mt-1 shrink-0" aria-hidden="true" />
          </a>
        ) : (
          <span className="font-bold">{c.source_title}</span>
        )}
        <br />
        <span className="text-ink-faint">{tc(`tier.${c.tier_label}` as 'tier.Regulator')}</span>
        {c.fictional ? <span className="block text-[13px] font-bold text-blank">{t('fictional')}</span> : null}
      </td>
      <td className="border-b border-rule px-4 py-3.5 align-top">
        <span
          className={cls(
            'inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-[13px] font-bold',
            LEVEL_CHIP[c.level] ?? LEVEL_CHIP.draft,
          )}
        >
          {tc(`level.${c.level}` as 'level.gold')}
        </span>
        {c.checked_on ? (
          <span className="mt-1 block whitespace-nowrap text-[13.5px] text-ink-faint">{dayMonth(c.checked_on, locale)}</span>
        ) : null}
      </td>
      <td className={cls('whitespace-nowrap border-b border-rule pe-5 ps-4 py-3.5 align-top', !c.fresh && 'font-bold text-pencil')}>
        {c.fresh_until ? shortDate(c.fresh_until, locale) : '—'}
        {!c.fresh ? <span className="block text-[13px]">{tc('notFresh')}</span> : null}
      </td>
    </tr>
  );
}

export function SourcesScreen({ caseId }: { caseId: string }) {
  const t = useTranslations('sources');
  const locale = useLocale();
  const q = useSources(caseId);
  const [inst, setInst] = useState('');
  const [level, setLevel] = useState('');
  const [search, setSearch] = useState('');
  const view = q.data;
  const rows = useMemo(() => {
    if (!view) return [];
    const s = search.trim().toLowerCase();
    return view.rows.filter((r) => {
      if (inst && !(r.cite.institution_id === inst || r.institutions.includes(view.institutions.find((i) => i.id === inst)?.name ?? '∅')))
        return false;
      if (level === 'attention' && r.cite.fresh) return false;
      if (level && level !== 'attention' && r.cite.level !== level) return false;
      if (s && !`${r.cite.text} ${r.cite.quote} ${r.cite.quote_translation ?? ''} ${r.cite.source_title}`.toLowerCase().includes(s))
        return false;
      return true;
    });
  }, [view, inst, level, search]);
  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  const field = 'min-h-11 rounded-[10px] border border-rule-strong bg-sheet px-2.5 text-[16px] text-ink';
  return (
    <div className="flex flex-col gap-[22px]">
      <header>
        <h1 className={h1Class}>{t('title')}</h1>
        <p className="m-0 mt-2 text-ink-soft">{t('lede')}</p>
      </header>
      {!view ? (
        <Skeleton className="h-96" />
      ) : (
        <>
          <Levels view={view} />
          <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => e.preventDefault()}>
            <label className="flex flex-col gap-1 text-[14px] text-ink-soft">
              {t('institution')}
              <select value={inst} onChange={(e) => setInst(e.target.value)} className={field}>
                <option value="">{t('allInstitutions')}</option>
                {view.institutions.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[14px] text-ink-soft">
              {t('checkedFilter')}
              <select value={level} onChange={(e) => setLevel(e.target.value)} className={field}>
                <option value="">{t('anyLevel')}</option>
                {view.counts.map((c) => (
                  <option key={c.level} value={c.level}>
                    {c.label}
                  </option>
                ))}
                <option value="attention">{t('attention')}</option>
              </select>
            </label>
            <label className="flex min-w-60 flex-1 flex-col gap-1 text-[14px] text-ink-soft">
              {t('search')}
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t('searchHint')}
                className={cls(field, 'px-3')}
              />
            </label>
          </form>
          <Sheet className="overflow-x-auto">
            <table className="w-full min-w-[900px] border-collapse text-[15px]">
              <thead>
                <tr className="text-ink-faint">
                  <th scope="col" className="w-[24%] border-b-[1.5px] border-rule-strong py-3 pe-4 ps-5 text-start font-semibold">
                    {t('colRule')}
                  </th>
                  <th scope="col" className="w-[38%] border-b-[1.5px] border-rule-strong px-4 py-3 text-start font-semibold">
                    {t('colQuote')}
                  </th>
                  <th scope="col" className="border-b-[1.5px] border-rule-strong px-4 py-3 text-start font-semibold">
                    {t('colSource')}
                  </th>
                  <th scope="col" className="border-b-[1.5px] border-rule-strong px-4 py-3 text-start font-semibold">
                    {t('colChecked')}
                  </th>
                  <th scope="col" className="border-b-[1.5px] border-rule-strong pe-5 ps-4 py-3 text-start font-semibold">
                    {t('colFresh')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <Row key={r.cite.id} r={r} locale={locale} />
                ))}
                {!level || level === 'draft'
                  ? view.researching.map((text, i) => (
                      <tr key={`r${i}`}>
                        <td className="border-b border-rule py-3.5 pe-4 ps-5 align-top font-bold">{text.split(':')[0]}</td>
                        <td className="border-b border-rule px-4 py-3.5 align-top text-ink-soft">
                          {text.split(':').slice(1).join(':').trim() || t('researchingNote')}
                        </td>
                        <td className="border-b border-rule px-4 py-3.5 align-top text-ink-faint">{t('notYet')}</td>
                        <td className="border-b border-rule px-4 py-3.5 align-top">
                          <span className={cls('rounded-full px-2 py-0.5 text-[13px] font-bold', LEVEL_CHIP.draft)}>
                            {t('researching')}
                          </span>
                        </td>
                        <td className="border-b border-rule pe-5 ps-4 py-3.5 align-top text-ink-faint">{t('noneYet')}</td>
                      </tr>
                    ))
                  : null}
              </tbody>
            </table>
            {!rows.length ? <p className="m-0 px-5 py-4 text-ink-soft">{t('noMatch')}</p> : null}
          </Sheet>
        </>
      )}
    </div>
  );
}
