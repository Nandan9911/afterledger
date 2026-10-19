'use client';
/**
 * Leads: what the papers point to that nobody listed. Each lead shows the statement lines behind it, what the
 * investigator thinks and what the critic says could be wrong, and the family marks it (checkpoint C4). Beside
 * it, what families in this country usually hold, checked against the case. On a phone, one lead at a time.
 */
import type { CoverageItem, LeadsView, LeadView } from '@afterledger/contracts';
import { ChevronLeft } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useSearchParams } from 'next/navigation';
import { useState } from 'react';
import { toast } from 'sonner';
import { useCase } from '@/components/CaseShell';
import { Button, ErrorNote, h1Class, h2Class, Sheet, Skeleton } from '@/components/ui';
import { Why } from '@/components/Why';
import { Link, usePathname, useRouter } from '@/i18n/navigation';
import { api, unwrap } from '@/lib/api';
import { perform } from '@/lib/changes';
import { asDate, cls, countryName, intl, listOf, localText, personName } from '@/lib/format';
import { useLeads, useMarkLead } from '@/lib/queries';
import { useMutation, useQueryClient } from '@tanstack/react-query';

type Tab = 'open' | 'confirmed' | 'closed';

const LABEL_LOOK: Record<string, string> = {
  sure: 'bg-ledger-tint text-ledger border-[1.5px] border-solid border-ledger',
  likely: 'bg-pencil-tint text-pencil border-[1.5px] border-dashed border-pencil',
  possible: 'bg-sheet text-blank border-[1.5px] border-dotted border-blank',
  unlikely: 'bg-sheet text-blank border-[1.5px] border-dotted border-blank',
  owed: 'bg-debit-tint text-debit border-[1.5px] border-solid border-debit',
};

function LabelPill({ lv }: { lv: LeadView }) {
  const t = useTranslations('leads');
  const tm = useTranslations('money');
  const debt = lv.lead.kind === 'debt';
  return (
    <span
      className={cls('inline-block shrink-0 rounded-full px-2.5 py-0.5 text-[14px] font-bold', LABEL_LOOK[debt ? 'owed' : lv.lead.label])}
    >
      {debt ? tm('owed') : t(`label.${lv.lead.label}` as 'label.sure')}
    </span>
  );
}

function shortDate(iso: string, locale: string) {
  const d = asDate(iso);
  return d ? new Intl.DateTimeFormat(intl(locale), { day: 'numeric', month: 'short', year: '2-digit', timeZone: 'UTC' }).format(d) : iso;
}

function Evidence({ lv }: { lv: LeadView }) {
  const t = useTranslations('leads');
  const tc = useTranslations('common');
  const locale = useLocale();
  const { caseId } = useCase();
  if (!lv.rows.length) return null;
  const first = lv.rows[0];
  const pages = [...new Set(lv.rows.map((r) => r.page_number).filter((n): n is number => !!n))].sort((a, b) => a - b);
  const paper = first.paper_title || lv.papers[0] || '';
  return (
    <figure className="m-0 mt-4 overflow-hidden rounded-[10px] border border-rule">
      {lv.rows.map((r) => (
        <div
          key={r.id}
          className="grid grid-cols-[80px_minmax(0,1fr)_auto] gap-3 border-b border-rule bg-[#FFF8D6] px-3.5 py-2 text-[15px] tabular last:border-b-0 sm:grid-cols-[96px_minmax(0,1fr)_auto]"
        >
          <span>{shortDate(r.date, locale)}</span>
          <span dir="auto" className="truncate font-bold">
            {r.narration}
          </span>
          <span className={cls('text-end font-bold', r.direction === 'credit' ? 'text-ledger' : '')}>{r.amount.text}</span>
        </div>
      ))}
      <figcaption className="border-t border-rule px-3.5 py-2 text-[14px] text-ink-soft">
        {pages.length ? t('rowsFrom', { paper, pages: pages.join(', '), count: pages.length }) : paper}{' '}
        {first.paper_id ? (
          <Link
            href={`/cases/${caseId}/papers?paper=${first.paper_id}${first.page_number && first.page_number > 1 ? `&page=${first.page_number}` : ''}`}
            className="tap font-bold"
          >
            {tc('openPage')}
          </Link>
        ) : null}
      </figcaption>
    </figure>
  );
}

function MarkButtons({ lv, big }: { lv: LeadView; big?: boolean }) {
  const t = useTranslations('leads');
  const tt = useTranslations('task');
  const { caseId, viewerId, readOnly } = useCase();
  const mark = useMarkLead(caseId);
  const off = mark.isPending || readOnly;
  const done = () => tt('marked');
  const plural = /\b(bonds|shares|units|policies|accounts|certificates|deposits|funds)\b/i.test(lv.lead.title);
  const run = (m: 'know' | 'check' | 'not_relevant') => perform(mark, { leadId: lv.lead.id, mark: m, by: viewerId }, { done });
  const debt = lv.lead.kind === 'debt';
  if (big) {
    return (
      <div className="flex flex-col gap-2.5">
        <Button size="lg" disabled={off} onClick={() => run('know')}>
          {plural ? t('knowThese') : t('know')}
        </Button>
        <div className="grid grid-cols-2 gap-2.5">
          <Button size="lg" variant="secondary" disabled={off} onClick={() => run('check')}>
            {t('checkThis')}
          </Button>
          <Button size="lg" variant="secondary" disabled={off} onClick={() => run('not_relevant')}>
            {t('notRelevant')}
          </Button>
        </div>
      </div>
    );
  }
  const marked = lv.lead.family_mark;
  return (
    <div className="mt-4 flex flex-wrap items-center gap-2.5">
      <Button variant={marked === 'know' ? 'primary' : marked ? 'secondary' : 'primary'} disabled={off} onClick={() => run('know')}>
        {plural ? t('knowThese') : t('know')}
      </Button>
      <Button variant="secondary" disabled={off || marked === 'check'} onClick={() => run('check')}>
        {t('checkThis')}
      </Button>
      {debt ? null : (
        <Button variant="secondary" disabled={off} onClick={() => run('not_relevant')}>
          {t('notRelevant')}
        </Button>
      )}
      {marked === 'check' ? <span className="text-[15px] font-bold text-pencil">{t('markedCheck')}</span> : null}
    </div>
  );
}

function LeadCard({ lv, view, first, tab }: { lv: LeadView; view: LeadsView; first: boolean; tab: Tab }) {
  const t = useTranslations('leads');
  const locale = useLocale();
  const { caseId } = useCase();
  const [open, setOpen] = useState(first);
  const l = lv.lead;
  const title = localText(l.title, l.title_local, l.lang_local, locale);
  const sentence = localText(l.sentence, l.sentence_local, l.lang_local, locale);
  const detail = !!(lv.rows.length || l.investigator_note || l.critic_note || l.next_step_note);
  return (
    <article
      id={`lead-${l.id}`}
      className={cls('scroll-mt-6 rounded-[14px] border border-rule bg-sheet', first ? 'px-6 py-[22px]' : 'px-6 py-5')}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className={cls('m-0 font-bold leading-snug', first ? 'text-[22px]' : 'text-[20px]')}>
          <Link href={`/cases/${caseId}/leads?lead=${l.id}`} className="tap text-ink no-underline lg:pointer-events-none">
            {title}
          </Link>
        </h2>
        <LabelPill lv={lv} />
      </div>
      {l.kind === 'debt' && lv.value ? (
        <p className="m-0 mt-1 text-[17px] font-bold text-debit">
          {t('aboutLeft', { amount: lv.value.short })}
          {l.family_mark ? <span className="ms-2 text-[14px] font-normal text-ink-soft">{t('valueFamily')}</span> : null}
        </p>
      ) : lv.value ? (
        <p className="m-0 mt-1 text-[17px] font-bold tabular">{lv.value.short}</p>
      ) : null}
      {!open || !l.investigator_note ? <p className="m-0 mt-2 text-[16px] text-ink-soft">{sentence}</p> : null}
      {open ? (
        <>
          <Evidence lv={lv} />
          {l.investigator_note || l.critic_note ? (
            <div className="mt-4 grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr))]">
              {l.investigator_note ? (
                <div>
                  <h3 className="m-0 text-[15px] font-bold text-ink-faint">{t('investigator')}</h3>
                  <p className="m-0 mt-1 text-[16px]">{l.investigator_note}</p>
                </div>
              ) : null}
              {l.critic_note ? (
                <div>
                  <h3 className="m-0 text-[15px] font-bold text-ink-faint">{t('critic')}</h3>
                  <p className="m-0 mt-1 text-[16px]">{l.critic_note}</p>
                </div>
              ) : null}
            </div>
          ) : null}
          {l.next_step_note ? (
            <p className="m-0 mt-4 rounded-[10px] bg-paper px-3.5 py-3 text-[16px]">
              {l.next_step_note} <Why ids={l.cites} cites={view.cites} title={title} />
            </p>
          ) : null}
        </>
      ) : detail ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="tap mt-2 text-[15px] font-bold text-ledger underline-offset-4 hover:underline"
        >
          {t('showWhy')}
        </button>
      ) : null}
      {tab === 'open' ? <MarkButtons lv={lv} /> : null}
      {tab === 'confirmed' ? (
        <p className="m-0 mt-3 text-[15px] font-bold text-ledger">
          {t('markedKnow')}
          {lv.institution ? ` · ${lv.institution}` : ''}
        </p>
      ) : null}
      {tab === 'closed' && l.closed_reason ? (
        <p className="m-0 mt-3 text-[15px] text-ink-soft">{t('closedBecause', { reason: l.closed_reason })}</p>
      ) : null}
    </article>
  );
}

function CoverageRow({ item, asker }: { item: CoverageItem; asker: string | null }) {
  const t = useTranslations('leads');
  const tc = useTranslations('common');
  const { caseId, viewerId } = useCase();
  const qc = useQueryClient();
  const [asking, setAsking] = useState(false);
  const answer = useMutation({
    mutationFn: (a: 'yes' | 'no' | 'unsure') =>
      unwrap(
        api().POST('/v1/cases/{case_id}/coverage/{item_id}/answer', {
          params: { path: { case_id: caseId, item_id: item.id } },
          body: { answer: a, by: viewerId },
        }),
      ),
    onSuccess: () => {
      toast.success(t('coverageThanks'));
      setAsking(false);
      qc.invalidateQueries({ queryKey: ['case', caseId] });
    },
  });
  const tone = {
    found: 'font-bold text-ledger',
    lead: 'font-bold text-pencil',
    not_seen: 'font-bold text-blank',
    not_applicable: 'text-ink-soft',
  }[item.status];
  const short = item.status === 'not_seen' ? t('coverage.not_seen') : item.detail;
  return (
    <li className="border-t border-rule py-2.5">
      <div className="flex justify-between gap-3">
        <span>{item.label}</span>
        <span className={cls('text-end', tone)}>{short}</span>
      </div>
      {item.status === 'not_seen' && item.detail ? <p className="m-0 mt-1.5 text-[15px] text-ink-soft">{item.detail}</p> : null}
      {item.ask ? (
        asking ? (
          <div className="mt-2 rounded-[10px] bg-paper p-3">
            <p className="m-0 font-bold">{item.ask}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="dark" disabled={answer.isPending} onClick={() => answer.mutate('yes')}>
                {tc('yes')}
              </Button>
              <Button size="sm" variant="secondary" disabled={answer.isPending} onClick={() => answer.mutate('no')}>
                {tc('no')}
              </Button>
              <Button size="sm" variant="secondary" disabled={answer.isPending} onClick={() => answer.mutate('unsure')}>
                {t('notSure')}
              </Button>
            </div>
          </div>
        ) : (
          <Button size="sm" variant="secondary" className="mt-2" onClick={() => setAsking(true)}>
            {asker ? t('askPerson', { name: asker }) : t('askFamily')}
          </Button>
        )
      ) : null}
    </li>
  );
}

function Coverage({ view }: { view: LeadsView }) {
  const t = useTranslations('leads');
  const locale = useLocale();
  const { header } = useCase();
  if (!view.coverage.length) return null;
  const claimant = header?.family.find((p) => p.roles.includes('claimant'));
  return (
    <aside aria-labelledby="usual" className="min-w-0 flex-[1_1_300px] rounded-[14px] border border-rule bg-sheet px-[22px] py-5">
      <h2 id="usual" className={h2Class}>
        {t('usual', {
          country: listOf(
            (view.countries.length ? view.countries : [view.country ?? '']).map((c) => countryName(c, locale)),
            locale,
          ),
        })}
      </h2>
      <p className="m-0 mt-1.5 text-[15px] text-ink-soft">{t('usualLede')}</p>
      <ul className="m-0 mt-3.5 list-none p-0 text-[16px]">
        {view.coverage.map((c) => (
          <CoverageRow key={c.id} item={c} asker={claimant ? personName(claimant, locale) : null} />
        ))}
      </ul>
    </aside>
  );
}

/** One lead, full screen, as on a phone: the evidence, what we think, what could be wrong, three answers. */
function OneLead({ view, leadId, list }: { view: LeadsView; leadId: string; list: LeadView[] }) {
  const t = useTranslations('leads');
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const index = list.findIndex((x) => x.lead.id === leadId);
  const lv = list[index] ?? [...view.confirmed, ...view.closed].find((x) => x.lead.id === leadId);
  if (!lv) return null;
  const l = lv.lead;
  const back = () => router.replace(pathname, { scroll: false });
  return (
    <div className="flex min-h-[70vh] flex-col gap-3 lg:hidden">
      <div className="flex items-center justify-between">
        <button
          type="button"
          aria-label={t('backToLeads')}
          onClick={back}
          className="grid h-11 w-11 place-items-center rounded-xl border border-rule-strong bg-sheet"
        >
          <ChevronLeft size={18} aria-hidden="true" className="rtl:rotate-180" />
        </button>
        {index >= 0 ? <span className="text-[15px] text-ink-soft">{t('leadOf', { n: index + 1, total: list.length })}</span> : <span />}
        <div aria-hidden="true" className="flex gap-1.5">
          {list.map((x, i) => (
            <span
              key={x.lead.id}
              className={cls('h-2 w-2 rounded-full', i === index ? 'bg-ink' : x.lead.family_mark ? 'bg-ledger' : 'bg-rule-strong')}
            />
          ))}
        </div>
      </div>
      <div>
        <LabelPill lv={lv} />
        <h1 className="m-0 mt-2 font-serif text-[27px] font-semibold leading-tight">
          {localText(l.title, l.title_local, l.lang_local, locale)}
        </h1>
      </div>
      <Evidence lv={lv} />
      <section>
        <h2 className="m-0 text-[15px] font-bold text-ink-faint">{t('whatWeThink')}</h2>
        <p className="m-0 mt-0.5 text-[16px]">{l.investigator_note || localText(l.sentence, l.sentence_local, l.lang_local, locale)}</p>
      </section>
      {l.critic_note ? (
        <section>
          <h2 className="m-0 text-[15px] font-bold text-ink-faint">{t('couldBeWrong')}</h2>
          <p className="m-0 mt-0.5 text-[16px]">{l.critic_note}</p>
        </section>
      ) : null}
      {l.status === 'open' ? (
        <div className="sticky bottom-[78px] mt-auto rounded-2xl border border-rule bg-sheet p-3">
          <MarkButtons lv={lv} big />
        </div>
      ) : null}
    </div>
  );
}

export function LeadsScreen({ caseId }: { caseId: string }) {
  const t = useTranslations('leads');
  const q = useLeads(caseId);
  const params = useSearchParams();
  const view = q.data;
  const [tab, setTab] = useState<Tab | null>(null);
  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  if (!view) {
    return (
      <div className="flex flex-col gap-5">
        <Skeleton className="h-12 w-60" />
        <Skeleton className="h-80" />
      </div>
    );
  }
  const current: Tab = tab ?? (view.open.length ? 'open' : 'confirmed');
  const list = view[current];
  const one = params.get('lead');
  return (
    <>
      {one ? <OneLead view={view} leadId={one} list={view.open} /> : null}
      <div className={cls('flex-col gap-[22px]', one ? 'hidden lg:flex' : 'flex')}>
        <header>
          <h1 className={h1Class}>{t('title')}</h1>
          <p className="m-0 mt-2 max-w-[46em] text-ink-soft">{t('lede')}</p>
        </header>
        <div role="tablist" aria-label={t('status')} className="flex flex-wrap gap-2">
          {(['open', 'confirmed', 'closed'] as const).map((k) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={current === k}
              onClick={() => setTab(k)}
              className={cls(
                'min-h-11 rounded-full border-[1.5px] px-4',
                current === k ? 'border-ink bg-ink font-bold text-white' : 'border-rule-strong bg-sheet text-ink',
              )}
            >
              {t(k, { count: view[k].length })}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-start gap-6">
          <div role="tabpanel" className="flex min-w-0 flex-[2_1_560px] flex-col gap-4">
            {list.length ? (
              list.map((lv, i) => <LeadCard key={lv.lead.id} lv={lv} view={view} first={i === 0 && current === 'open'} tab={current} />)
            ) : (
              <Sheet className="px-6 py-5">
                <p className="m-0 text-ink-soft">{current === 'open' ? t('none') : t('noneHere')}</p>
              </Sheet>
            )}
          </div>
          <Coverage view={view} />
        </div>
      </div>
    </>
  );
}
