'use client';
/**
 * One pack: every form and letter, the page as it was filled with a numbered marker on each value, what each
 * value is and where it came from, the papers to attach, and the checks run on the finished files. The family
 * approves the pack or says what needs changing (checkpoint C6).
 */
import type { CheckResult, FilledField, PackItemView, PackView } from '@afterledger/contracts';
import { Check, ChevronLeft, ChevronRight, Download, ExternalLink, X } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useState } from 'react';
import { toast } from 'sonner';
import { useCase } from '@/components/CaseShell';
import { Button, ErrorNote, Skeleton } from '@/components/ui';
import { Link } from '@/i18n/navigation';
import { apiUrl } from '@/lib/api';
import { cls, dayMonth, institutionLabel, languageName, longDate, personName } from '@/lib/format';
import { useAnswer, useClaimEvent, usePack } from '@/lib/queries';
import { PACK_STATUS_LOOK } from './PacksScreen';

const CHECK_KEYS = new Set(['readback', 'letter_fidelity', 'rules_fresh', 'back_translation']);
const HANDED_IN = new Set(['submitted', 'acknowledged', 'settled', 'returned', 'escalated']);

function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Once the papers are approved, the family hands them in (or posts them) and says so here: the claim is then
 * handed in, its clocks start, and the tracker and the institution's desk follow it. */
function HandIn({ view }: { view: PackView }) {
  const t = useTranslations('pack');
  const tc = useTranslations('common');
  const tn = useTranslations('nav');
  const locale = useLocale();
  const { caseId, viewerId } = useCase();
  const event = useClaimEvent(caseId);
  const [open, setOpen] = useState(false);
  const [on, setOn] = useState('');
  const claim = view.claim;
  if (HANDED_IN.has(claim.status) || claim.submitted_on) {
    return (
      <span className="inline-flex min-h-12 flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-bold text-ledger">
          {claim.submitted_on ? t('handedIn', { date: dayMonth(claim.submitted_on, locale) }) : t('handedInPlain')}
        </span>
        <Link href={`/cases/${caseId}/tracker`} className="tap font-bold">
          {tn('tracker')}
        </Link>
      </span>
    );
  }
  if (!open) {
    return (
      <Button size="lg" onClick={() => setOpen(true)}>
        {t('handIn')}
      </Button>
    );
  }
  return (
    <form
      className="flex w-full flex-col gap-2 sm:w-auto"
      onSubmit={(e) => {
        e.preventDefault();
        event.mutate(
          { claimId: claim.id, event: 'submitted', on: on || null, by: viewerId },
          { onSuccess: () => (setOpen(false), toast.success(t('handInDone'))) },
        );
      }}
    >
      <label className="flex flex-col gap-1 text-[15px] font-bold">
        {t('handInWhen')}
        <input
          type="date"
          value={on}
          max={localToday()}
          onChange={(e) => setOn(e.target.value)}
          className="min-h-11 rounded-[10px] border border-rule-strong bg-sheet px-2.5 text-[16px] font-normal"
        />
      </label>
      <div className="flex gap-2">
        <Button type="submit" disabled={event.isPending}>
          {tc('save')}
        </Button>
        <Button variant="secondary" disabled={event.isPending} onClick={() => setOpen(false)}>
          {tc('cancel')}
        </Button>
      </div>
    </form>
  );
}

function Enclosure({ status, name, note }: { status: string; name: string; note: string }) {
  const t = useTranslations('pack');
  const have = status === 'have';
  const unknown = status === 'unknown';
  return (
    <li className="grid grid-cols-[22px_minmax(0,1fr)] gap-2 border-t border-rule py-2">
      {have ? (
        <Check size={18} strokeWidth={2.6} className="mt-0.5 text-ledger" aria-label={t('haveIt')} />
      ) : (
        <span
          aria-label={unknown ? t('notKnown') : t('missing')}
          className={cls(
            'mt-0.5 h-[18px] w-[18px] rounded-full border-2',
            unknown ? 'border-dotted border-blank' : 'border-dashed border-pencil',
          )}
        />
      )}
      <span>
        {have ? name : <strong>{name}.</strong>} {note && !have ? note : null}
        {have && note && !/^Among the papers/.test(note) ? <span className="block text-[14px] text-ink-soft">{note}</span> : null}
      </span>
    </li>
  );
}

function Preview({ iv, mirror, page, setPage }: { iv: PackItemView; mirror: boolean; page: number; setPage: (n: number) => void }) {
  const t = useTranslations('pack');
  const pages = iv.pages;
  const n = Math.min(page, Math.max(pages, 1));
  const fields = iv.item.fields.filter((f) => f.page === n && f.box && f.box.length === 4);
  if (!iv.page_url || !pages) {
    return (
      <div className="grid min-h-60 place-items-center rounded-md bg-sheet p-6 text-center text-ink-soft">
        <p className="m-0">{iv.item.note || t('noPreview')}</p>
      </div>
    );
  }
  const src = apiUrl(`${iv.page_url}/${n}${mirror ? '?mirror=1' : ''}`);
  return (
    <div className="flex flex-col gap-3">
      <div className="relative overflow-hidden rounded-[4px] bg-white shadow-[0_16px_30px_-20px_rgba(20,32,43,0.45)]">
        {/* eslint-disable-next-line @next/next/no-img-element -- rendered by the API from the filled PDF */}
        <img src={src} alt={t('pageAlt', { title: iv.item.title, page: n })} className="block h-auto w-full" />
        {!mirror
          ? fields.map((f, i) => {
              const b = f.box as number[];
              const check = f.certainty !== 'sure' || f.readback_ok === false;
              return (
                <span key={f.key} aria-hidden="true">
                  <span
                    className="absolute rounded-[2px] bg-hl/45 mix-blend-multiply"
                    style={{
                      left: `${b[0] * 100}%`,
                      top: `${b[1] * 100}%`,
                      width: `${(b[2] - b[0]) * 100}%`,
                      height: `${(b[3] - b[1]) * 100}%`,
                    }}
                  />
                  <span
                    className={cls(
                      'absolute grid h-5 min-w-5 -translate-y-1/2 place-items-center rounded-full bg-sheet px-1 text-[11px] font-bold',
                      check ? 'border-[1.5px] border-dashed border-pencil text-pencil' : 'border-[1.5px] border-ledger text-ledger',
                    )}
                    style={{ left: `calc(${b[2] * 100}% + 4px)`, top: `${((b[1] + b[3]) / 2) * 100}%` }}
                  >
                    {i + 1}
                  </span>
                </span>
              );
            })
          : null}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="m-0 text-[14px] text-ink-soft">{t('pageOf', { page: n, pages })}</p>
        {pages > 1 ? (
          <div className="flex gap-2">
            <button
              type="button"
              disabled={n <= 1}
              onClick={() => setPage(n - 1)}
              className="inline-flex min-h-10 items-center gap-1 rounded-lg border border-rule-strong bg-sheet px-3 text-[15px] disabled:opacity-50"
            >
              <ChevronLeft size={16} aria-hidden="true" className="rtl:rotate-180" />
              {t('prev')}
            </button>
            <button
              type="button"
              disabled={n >= pages}
              onClick={() => setPage(n + 1)}
              className="inline-flex min-h-10 items-center gap-1 rounded-lg border border-rule-strong bg-sheet px-3 text-[15px] disabled:opacity-50"
            >
              {t('next')}
              <ChevronRight size={16} aria-hidden="true" className="rtl:rotate-180" />
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function FieldNotes({ fields }: { fields: FilledField[] }) {
  const t = useTranslations('pack');
  return (
    <ol className="m-0 flex list-none flex-col gap-2.5 p-0">
      {fields.map((f, i) => {
        const check = f.certainty !== 'sure' || f.readback_ok === false;
        return (
          <li
            key={f.key}
            className={cls(
              'rounded-xl bg-sheet px-3.5 py-2.5',
              check ? 'border-[1.5px] border-dashed border-pencil' : 'border border-rule',
            )}
          >
            <p className={cls('m-0 text-[15px] font-bold', check && 'text-pencil')}>
              {i + 1}. {f.label}
            </p>
            <p className="m-0 font-bold tabular">{f.value}</p>
            {f.note ? <p className="m-0 mt-0.5 text-[14px]">{f.note}</p> : null}
            <p className="m-0 mt-0.5 text-[13.5px] text-ink-soft">
              {[
                f.source === 'the case'
                  ? t('fromTheCase')
                  : f.source === 'the family'
                    ? t('fromFamily')
                    : f.source
                      ? t('fromCase', { source: f.source })
                      : null,
                f.readback_ok === true ? t('readBackOk') : f.readback_ok === false ? t('readBackFail') : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

function Approve({ view }: { view: PackView }) {
  const t = useTranslations('pack');
  const tc = useTranslations('common');
  const locale = useLocale();
  const { caseId, viewerId } = useCase();
  const answer = useAnswer(caseId);
  const [changing, setChanging] = useState(false);
  const [note, setNote] = useState('');
  const cp = view.checkpoint;
  if (view.pack.status === 'approved' || view.pack.status === 'submitted') {
    return (
      <>
        {view.approved_by && view.pack.approved_on ? (
          <span className="inline-flex min-h-12 items-center rounded-[10px] border-[1.5px] border-ledger bg-ledger-tint px-4 font-bold text-ledger">
            {t('approved', {
              name: personName(view.approved_by, locale),
              date: dayMonth(view.pack.approved_on, locale),
            })}
          </span>
        ) : null}
        <HandIn view={view} />
      </>
    );
  }
  if (!cp || cp.status !== 'open') return null;
  if (changing) {
    return (
      <form
        className="flex w-full flex-col gap-2 sm:w-auto"
        onSubmit={(e) => {
          e.preventDefault();
          if (!note.trim()) return;
          answer.mutate(
            { checkpointId: cp.id, answer: 'change', note: note.trim(), by: viewerId },
            { onSuccess: () => (setChanging(false), toast.success(t('changeSent'))) },
          );
        }}
      >
        <label className="flex flex-col gap-1 text-[15px] font-bold">
          {t('changeNote')}
          <textarea
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="min-w-72 rounded-[10px] border border-rule-strong bg-sheet p-2.5 text-[16px] font-normal"
          />
        </label>
        <div className="flex gap-2">
          <Button type="submit" variant="dark" disabled={!note.trim() || answer.isPending}>
            {tc('send')}
          </Button>
          <Button variant="secondary" onClick={() => setChanging(false)}>
            {tc('cancel')}
          </Button>
        </div>
      </form>
    );
  }
  return (
    <>
      <Button variant="secondary" size="lg" onClick={() => setChanging(true)}>
        {t('changeAsk')}
      </Button>
      <Button
        size="lg"
        disabled={answer.isPending}
        onClick={() =>
          answer.mutate({ checkpointId: cp.id, answer: 'approve', by: viewerId }, { onSuccess: () => toast.success(t('approvedToast')) })
        }
      >
        {t('approve')}
      </Button>
    </>
  );
}

export function PackScreen({ caseId, packId }: { caseId: string; packId: string }) {
  const t = useTranslations('pack');
  const tp = useTranslations('packs');
  const locale = useLocale();
  const q = usePack(caseId, packId);
  const [sel, setSel] = useState<string | null>(null);
  const [mirror, setMirror] = useState(false);
  const [pages, setPages] = useState<Record<string, number>>({});
  const view = q.data;
  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  if (!view) {
    return (
      <div className="flex flex-col gap-5">
        <Skeleton className="h-10 w-96" />
        <Skeleton className="h-[520px]" />
      </div>
    );
  }
  const p = view.pack;
  const current = view.items.find((i) => i.item.id === sel) ?? view.items.find((i) => i.pages > 0) ?? view.items[0];
  const institution = institutionLabel(view.institution, locale);
  /** A passed check, worded in the page's language from its figures; anything else as the server wrote it. */
  const checkDetail = (c: CheckResult) => {
    const v = (c.params ?? {}) as Record<string, unknown>;
    if (c.result !== 'pass') return c.detail;
    if (c.name === 'readback' && typeof v.matched === 'number' && typeof v.checked === 'number')
      return t('checkPass.readback', { matched: v.matched, checked: v.checked });
    if (c.name === 'letter_fidelity') return t('checkPass.letter_fidelity');
    if (c.name === 'back_translation' && typeof v.language === 'string')
      return t('checkPass.back_translation', { language: languageName(v.language, locale) || v.language });
    if (c.name === 'rules_fresh' && typeof v.checked_on === 'string')
      return t('checkPass.rules_fresh', { date: longDate(v.checked_on, locale) });
    return c.detail;
  };
  const signers = current?.signers.map((s) => personName(s, locale)) ?? [];
  const mirrorLang = current?.item.mirror_language;
  const page = current ? Math.min(pages[current.item.id] ?? 1, Math.max(current.pages, 1)) : 1;
  const notes = current && !mirror ? current.item.fields.filter((f) => f.page === page && f.box && f.box.length === 4) : [];
  return (
    <div className="flex flex-col gap-[22px]">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="m-0 text-[15px]">
            <Link href={`/cases/${caseId}/packs`} className="tap font-bold">
              {t('crumb')}
            </Link>
            <span className="text-ink-faint"> / {institution}</span>
          </p>
          <h1 className="m-0 mt-1.5 font-serif text-[28px] font-semibold leading-tight sm:text-[34px]">{p.title}</h1>
          {p.summary ? <p className="m-0 mt-2 max-w-[46em] text-ink-soft">{p.summary}</p> : null}
          <span className={cls('mt-2.5 inline-block rounded-full px-2.5 py-0.5 text-[13px] font-bold', PACK_STATUS_LOOK[p.status])}>
            {tp(`status.${p.status}` as 'status.ready')}
          </span>
        </div>
        <div className="flex flex-wrap items-start gap-2.5">
          <a
            href={apiUrl(view.download_url)}
            className="inline-flex min-h-12 items-center gap-2 rounded-[10px] border border-rule-strong bg-sheet px-[18px] font-bold text-ink no-underline hover:text-ink"
          >
            <Download size={18} aria-hidden="true" />
            {t('download')}
          </a>
          <Approve view={view} />
        </div>
      </header>

      <div className="flex flex-wrap items-start gap-5">
        <section aria-labelledby="inpack" className="flex min-w-0 flex-[1_1_260px] flex-col gap-3 sm:max-w-[320px]">
          <h2 id="inpack" className="m-0 text-[17px] font-bold">
            {t('inPack')}
          </h2>
          <ol className="m-0 flex list-none flex-col gap-2 p-0">
            {view.items.map((iv) => {
              const on = iv.item.id === current?.item.id;
              return (
                <li key={iv.item.id}>
                  <button
                    type="button"
                    aria-current={on ? 'true' : undefined}
                    onClick={() => (setSel(iv.item.id), setMirror(false))}
                    className={cls(
                      'block w-full rounded-xl bg-sheet px-3.5 py-3 text-start',
                      on ? 'border-[1.5px] border-ink' : 'border border-rule hover:border-rule-strong',
                    )}
                  >
                    <span className="block font-bold">{iv.item.title}</span>
                    {iv.item.note ? <span className="block text-[14px] text-ink-soft">{iv.item.note}</span> : null}
                  </button>
                </li>
              );
            })}
          </ol>
          {p.enclosures.length ? (
            <>
              <h2 className="m-0 mt-2 text-[17px] font-bold">{t('attach')}</h2>
              <ul className="m-0 list-none p-0 text-[15.5px]">
                {p.enclosures.map((e, i) => (
                  <Enclosure key={i} status={e.status} name={e.name} note={e.note} />
                ))}
              </ul>
            </>
          ) : null}
          {p.submit_how ? (
            <div className="rounded-xl bg-blank-tint px-3.5 py-3">
              <h2 className="m-0 text-[15px] font-bold text-blank">{t('submitHow')}</h2>
              <p className="m-0 mt-0.5 text-[15px]">{p.submit_how}</p>
            </div>
          ) : null}
        </section>

        <section
          aria-label={t('preview')}
          className="flex min-w-0 flex-[3_1_440px] flex-col gap-3 rounded-[14px] border border-rule bg-[#E9EEEA] p-4"
        >
          {current ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 text-[15px]">
                <span className="font-bold">
                  {current.item.title}
                  {signers.length ? (
                    <span className="ms-2 font-normal text-ink-soft">
                      {t('signs', { names: signers.join(', '), count: signers.length })}
                    </span>
                  ) : null}
                </span>
                <span className="flex flex-wrap gap-2">
                  {current.mirror_url && mirrorLang ? (
                    <button
                      type="button"
                      onClick={() => setMirror((v) => !v)}
                      className="inline-flex min-h-10 items-center rounded-lg border border-rule-strong bg-sheet px-3 font-bold"
                    >
                      {mirror ? t('letterOriginal') : t('letterCopy', { language: languageName(mirrorLang, locale) })}
                    </button>
                  ) : null}
                  {current.file_url ? (
                    <a
                      href={apiUrl(`${current.file_url}${mirror ? '?mirror=1' : ''}`)}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-rule-strong bg-sheet px-3 font-bold text-ink no-underline hover:text-ink"
                    >
                      {t('openPdf')}
                      <ExternalLink size={14} aria-hidden="true" />
                    </a>
                  ) : null}
                </span>
              </div>
              <Preview
                key={`${current.item.id}-${mirror}`}
                iv={current}
                mirror={mirror}
                page={page}
                setPage={(n) => setPages((m) => ({ ...m, [current.item.id]: n }))}
              />
            </>
          ) : null}
        </section>

        <aside aria-labelledby="checks" className="flex min-w-0 flex-[2_1_280px] flex-col gap-3.5">
          {notes.length ? (
            <section aria-labelledby="fields" className="flex flex-col gap-2.5">
              <h2 id="fields" className="m-0 text-[16px] font-bold">
                {t('fieldsTitle')}
              </h2>
              <FieldNotes fields={notes} />
            </section>
          ) : null}
          <section className="rounded-[14px] border border-rule bg-sheet px-[18px] py-4">
            <h2 id="checks" className="m-0 text-[16px] font-bold">
              {t('checks')}
            </h2>
            <ul className="m-0 mt-2 list-none p-0 text-[15.5px]">
              {p.checks.map((c, i) => (
                <li key={i} className="grid grid-cols-[22px_minmax(0,1fr)] gap-2 border-t border-rule py-1.5">
                  {c.result === 'pass' ? (
                    <Check size={18} strokeWidth={2.6} className="mt-0.5 text-ledger" aria-label={t('pass')} />
                  ) : c.result === 'fail' ? (
                    <X size={18} strokeWidth={2.6} className="mt-0.5 text-debit" aria-label={t('fail')} />
                  ) : (
                    <span
                      aria-label={t('notKnown')}
                      className="mt-0.5 h-[18px] w-[18px] rounded-full border-2 border-dotted border-blank"
                    />
                  )}
                  <span>
                    {CHECK_KEYS.has(c.name) ? t(`check.${c.name}` as 'check.readback') : c.name}: <strong>{checkDetail(c)}</strong>
                  </span>
                </li>
              ))}
            </ul>
          </section>
          {view.claim?.plan_id ? (
            <Link href={`/cases/${caseId}/plans/${view.claim.plan_id}`} className="tap text-[15px] font-bold">
              {t('seePlan')}
            </Link>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
