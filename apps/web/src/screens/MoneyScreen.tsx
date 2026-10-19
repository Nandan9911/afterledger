'use client';
/**
 * The money map: everything held or owed, one sheet per country, each amount in its own currency. A pill
 * says whether a holding is confirmed, a lead, owed or paid; the line beside it says what decides its claim.
 */
import type { CountryGroup, HoldingView, MoneyView } from '@afterledger/contracts';
import { useLocale, useTranslations } from 'next-intl';
import { useCase } from '@/components/CaseShell';
import { ErrorNote, h1Class, h2Class, Sheet, Skeleton } from '@/components/ui';
import { Why } from '@/components/Why';
import { Link } from '@/i18n/navigation';
import { cls, countryName, intl, localText, personName } from '@/lib/format';
import { useMoney } from '@/lib/queries';

/** "Indian rupees", "Singapore dollars": the currency's plural name in the reader's language. */
function currencyName(code: string, locale: string): string {
  try {
    const parts = new Intl.NumberFormat(intl(locale), { style: 'currency', currency: code, currencyDisplay: 'name' }).formatToParts(2);
    return parts.find((p) => p.type === 'currency')?.value ?? code;
  } catch {
    return code;
  }
}

const TAG_LOOK: Record<HoldingView['tag'], string> = {
  confirmed: 'bg-ledger-tint text-ledger border-[1.5px] border-solid border-ledger',
  lead: 'bg-pencil-tint text-pencil border-[1.5px] border-dashed border-pencil',
  owed: 'bg-debit-tint text-debit border-[1.5px] border-solid border-debit',
  paid: 'bg-ledger text-white border-[1.5px] border-solid border-ledger',
  closed: 'bg-sheet text-blank border-[1.5px] border-dotted border-blank',
};

function TagPill({ tag, className }: { tag: HoldingView['tag']; className?: string }) {
  const t = useTranslations('money');
  return <span className={cls('inline-block rounded-full px-2 py-px text-[13px] font-bold', TAG_LOOK[tag], className)}>{t(tag)}</span>;
}

function HoldingRow({ hv }: { hv: HoldingView }) {
  const t = useTranslations('money');
  const tc = useTranslations('common');
  const { caseId } = useCase();
  const h = hv.holding;
  const mask = h.account_mask ? (/^ending\b/i.test(h.account_mask) ? h.account_mask : t('ending', { mask: h.account_mask })) : null;
  const title = mask ? `${h.label}, ${mask}` : h.label;
  const href =
    hv.tag === 'lead' && hv.lead ? `/cases/${caseId}/leads#lead-${hv.lead.id}` : hv.plan_id ? `/cases/${caseId}/plans/${hv.plan_id}` : null;
  const local = hv.lead && hv.lead.title_local && hv.lead.lang_local ? hv.lead.title_local : null;
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 border-t border-rule px-5 py-3">
      <div className="min-w-0">
        <p className="m-0 font-bold">
          {href ? (
            <Link href={href} className="tap text-ink no-underline hover:text-ledger hover:underline">
              {title}
            </Link>
          ) : (
            title
          )}
        </p>
        {local ? (
          <p lang={hv.lead?.lang_local} className="m-0 text-[15px] text-ink-soft">
            {local}
          </p>
        ) : null}
        {hv.note ? <p className="m-0 mt-0.5 text-[15px] text-ink-soft">{hv.note}</p> : null}
      </div>
      <div className="double-rule ps-3.5 text-end">
        {hv.value ? (
          <p className={cls('m-0 font-bold tabular', hv.tag === 'owed' && 'text-debit', hv.certainty === 'check' && 'text-pencil')}>
            {hv.tag === 'owed' ? t('owedSuffix', { amount: hv.value.text }) : hv.value.text}
          </p>
        ) : (
          <p className="m-0 text-ink-soft">{tc('valueNotKnown')}</p>
        )}
        {hv.certainty === 'check' && hv.value ? <p className="m-0 text-[13px] text-pencil">{t('familyFigure')}</p> : null}
        <TagPill tag={hv.tag} className="mt-1" />
      </div>
    </li>
  );
}

function CountrySheet({ group, locale }: { group: CountryGroup; locale: string }) {
  const t = useTranslations('money');
  const person = group.claimant;
  const name = person ? personName(person, locale) : null;
  const handles = person && !person.roles.includes('claimant');
  const who = name
    ? person?.city
      ? t(handles ? 'handles' : 'claims', { name, city: person.city })
      : t(handles ? 'handlesNoCity' : 'claimsNoCity', { name })
    : '';
  const id = `country-${group.country}`;
  return (
    <section aria-labelledby={id} className="overflow-hidden rounded-[14px] border border-rule bg-sheet">
      <div className="border-b-2 border-ink px-5 py-[18px]">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 id={id} className="m-0 font-serif text-[26px] font-semibold">
            {countryName(group.country, locale)}
          </h2>
          <p className="m-0 text-[22px] font-bold tabular">{group.total_known.short}</p>
        </div>
        <p className="m-0 mt-1 text-[15px] text-ink-soft">
          {currencyName(group.currency, locale)}. {who}
        </p>
        {group.owed ? <p className="m-0 mt-0.5 text-[15px] font-bold text-debit">{t('owedTotal', { amount: group.owed.short })}</p> : null}
      </div>
      {group.institutions.map((g, i) => (
        <div key={`${g.institution_id ?? g.name}-${i}`}>
          <h3 className={cls('m-0 px-5 pb-1 pt-3.5 text-[16px] font-bold', i > 0 && 'border-t border-rule')}>
            {g.from_paper ? t('fromNotes', { paper: g.from_paper }) : g.role ? `${g.name}, ${g.role}` : g.name}
          </h3>
          <ul className="m-0 list-none p-0">
            {g.holdings.map((hv) => (
              <HoldingRow key={hv.holding.id} hv={hv} />
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

function Rules({ view, locale }: { view: MoneyView; locale: string }) {
  const t = useTranslations('money');
  const summary = localText(view.summary, view.summary_local, view.lang_local, locale);
  if (!summary && !view.notes.length) return null;
  return (
    <Sheet
      className={cls('grid gap-4 px-6 py-5', view.contested && 'border-[1.5px] border-dashed border-pencil bg-pencil-tint')}
      aria-labelledby="rules"
    >
      {summary ? (
        <div>
          <h2 id="rules" className={h2Class}>
            {view.contested ? t('contested') : t('rulesApply')}
          </h2>
          <p className="m-0 mt-1.5 max-w-[60em]">{summary}</p>
        </div>
      ) : null}
      {view.notes.length ? (
        <div>
          <h2 className={h2Class}>{t('papersFrom')}</h2>
          {view.notes.map((n, i) => (
            <p key={i} className="m-0 mt-1.5 max-w-[60em]">
              {n.text} <Why ids={n.cites} cites={view.cites} title={t('papersFrom')} />
            </p>
          ))}
        </div>
      ) : null}
    </Sheet>
  );
}

export function MoneyScreen({ caseId }: { caseId: string }) {
  const t = useTranslations('money');
  const locale = useLocale();
  const { header } = useCase();
  const q = useMoney(caseId);
  const view = q.data;
  const deceased = header?.deceased;
  const name = deceased ? personName(deceased, locale) : null;
  // the key explains the marks this map shows (paid and closed only once something is)
  const shown = new Set(view?.countries.flatMap((g) => g.institutions.flatMap((i) => i.holdings.map((h) => h.tag))) ?? []);
  const keys = (['confirmed', 'lead', 'owed', 'paid', 'closed'] as const).filter(
    (k) => shown.has(k) || k === 'confirmed' || k === 'lead' || k === 'owed',
  );
  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className={h1Class}>{t('title')}</h1>
          <p className="m-0 mt-2 max-w-[44em] text-ink-soft">{name ? t('lede', { name }) : t('ledeShort')}</p>
        </div>
        <div role="list" aria-label={t('key')} className="flex flex-wrap gap-2.5">
          {keys.map((k) => (
            <span role="listitem" key={k}>
              <TagPill tag={k} className="px-2.5 py-0.5 text-[14px]" />
            </span>
          ))}
        </div>
      </header>
      {!view ? (
        <div className="grid gap-6 [grid-template-columns:repeat(auto-fit,minmax(min(480px,100%),1fr))]">
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
        </div>
      ) : view.countries.length ? (
        <>
          <div className="grid items-start gap-6 [grid-template-columns:repeat(auto-fit,minmax(min(480px,100%),1fr))]">
            {view.countries.map((g) => (
              <CountrySheet key={g.country} group={g} locale={locale} />
            ))}
          </div>
          <Rules view={view} locale={locale} />
        </>
      ) : (
        <Sheet className="px-6 py-8 text-center">
          <p className="m-0 text-ink-soft">{t('empty')}</p>
          <Link href={`/cases/${caseId}/papers`} className="mt-2 inline-block font-bold">
            {t('addPapers')}
          </Link>
        </Sheet>
      )}
    </div>
  );
}
