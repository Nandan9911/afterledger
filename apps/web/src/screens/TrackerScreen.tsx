'use client';
/**
 * The tracker: every claim's clock on one time line with today marked, the letters waiting for approval with
 * their fidelity check and the family's-language copy, what to do if an institution does not answer, replies
 * received, and a read-only link for the institution's desk.
 */
import type { ClockRow, LetterView, TrackerView } from '@afterledger/contracts';
import { Copy, ExternalLink, Printer, Share2 } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useState, type FormEvent } from 'react';
import { toast } from 'sonner';
import { useCase } from '@/components/CaseShell';
import { Button, Chip, ErrorNote, h1Class, h2Class, Sheet, Skeleton } from '@/components/ui';
import { Why } from '@/components/Why';
import { apiUrl } from '@/lib/api';
import { asDate, cls, dayMonth, languageName, longDate, personName } from '@/lib/format';
import { useClaimEvent, useDraftLetter, useLetterAction, useRecordReply, useShare, useTracker } from '@/lib/queries';

const DAY = 86_400_000;

type Bar = { from: string; to: string; look: 'tint' | 'late' | 'paid' | 'met' | 'expected' };

function barsFor(row: ClockRow, today: string): Bar[] {
  const out: Bar[] = [];
  for (const c of row.clocks) {
    if (c.owner !== 'institution' || !c.started_on || !c.due_on) continue;
    if (c.status === 'late') {
      out.push({ from: c.started_on, to: c.due_on, look: 'tint' }, { from: c.due_on, to: today, look: 'late' });
    } else if (c.status === 'met' || c.status === 'ended') {
      out.push({ from: c.started_on, to: c.ended_on ?? c.due_on, look: row.settled_on ? 'paid' : 'met' });
    } else {
      out.push({ from: c.started_on, to: c.due_on, look: 'tint' });
    }
  }
  return out;
}

const BAR_LOOK: Record<Bar['look'], string> = {
  tint: 'bg-ledger-tint border-[1.5px] border-ledger',
  late: '',
  paid: 'bg-ledger',
  met: 'bg-ledger/70',
  expected: 'border-[1.5px] border-dashed border-blank bg-blank-tint/60',
};

function Timeline({ view }: { view: TrackerView }) {
  const t = useTranslations('tracker');
  const locale = useLocale();
  const start = asDate(view.start)!.getTime();
  const end = asDate(view.end)!.getTime();
  const span = Math.max(DAY, end - start);
  const pos = (iso: string) => Math.max(0, Math.min(100, ((asDate(iso)!.getTime() - start) / span) * 100));
  const todayAt = pos(view.today);
  // ticks on the 1st, 11th and 21st of each month, leaving room around today's mark (its label runs on after it)
  const ticks: string[] = [];
  const d = new Date(start);
  d.setUTCDate(1);
  while (d.getTime() <= end) {
    for (const day of [1, 11, 21]) {
      const tk = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), day));
      const iso = tk.toISOString().slice(0, 10);
      const gap = pos(iso) - todayAt;
      if (tk.getTime() >= start && tk.getTime() <= end && (gap < -8 || gap > 20) && pos(iso) < 94) ticks.push(iso);
    }
    d.setUTCMonth(d.getUTCMonth() + 1);
  }
  const expectedRows = view.expected
    .filter((e) => e.kind === 'expected' && !view.rows.some((r) => r.claim_id === e.claim_id && r.clocks.length))
    .map((e) => {
      const asked = [...view.received, ...view.letters]
        .map((l) => l.letter)
        .find((l) => l.claim_id === e.claim_id && l.direction === 'out');
      return { e, from: asked?.on ?? view.today };
    });
  const rows = view.rows.filter((r) => r.clocks.some((c) => c.owner === 'institution' && c.started_on));
  if (!rows.length && !expectedRows.length) return null;
  return (
    <Sheet as="section" className="overflow-x-auto px-4 py-5 sm:px-6" aria-labelledby="clocks">
      <h2 id="clocks" className="sr-only">
        {t('clocks')}
      </h2>
      {/* on a phone each claim's bar goes under its name, full width, with today marked on each bar */}
      <div className="md:min-w-[760px]">
        <div className="grid grid-cols-1 gap-4 border-b-[1.5px] border-rule-strong pb-2 text-[13px] text-ink-faint md:grid-cols-[230px_minmax(0,1fr)]">
          <span className="hidden md:block" />
          <div className="relative h-[18px]">
            {ticks.map((tk) => (
              <span
                key={tk}
                className={cls('absolute -translate-x-1/2 whitespace-nowrap rtl:translate-x-1/2', !tk.endsWith('-01') && 'max-md:hidden')}
                style={{ insetInlineStart: `${pos(tk)}%` }}
              >
                {dayMonth(tk, locale)}
              </span>
            ))}
            <span className="absolute whitespace-nowrap ps-1.5 font-bold text-ink" style={{ insetInlineStart: `${todayAt}%` }}>
              {t('todayMark', { date: dayMonth(view.today, locale) })}
            </span>
          </div>
        </div>
        <div className="relative">
          <div
            aria-hidden="true"
            className="absolute inset-y-0 hidden w-0.5 bg-ink md:block"
            style={{ insetInlineStart: `calc(230px + 16px + (100% - 246px) * ${todayAt / 100})` }}
          />
          {rows.map((r) => (
            <div
              key={r.claim_id}
              className="grid grid-cols-1 items-center gap-2 border-b border-rule py-3.5 last:border-b-0 md:grid-cols-[230px_minmax(0,1fr)] md:gap-4"
            >
              <div>
                <p className="m-0 font-bold">{r.institution || r.title}</p>
                <p
                  className={cls(
                    'm-0 text-[14px]',
                    r.late ? 'font-bold text-debit' : r.settled_on ? 'font-bold text-ledger' : 'text-ink-soft',
                  )}
                >
                  {r.status_text}
                </p>
              </div>
              <div className="relative h-[22px]" title={r.title}>
                <div
                  aria-hidden="true"
                  className="absolute -inset-y-1 z-[1] w-0.5 bg-ink md:hidden"
                  style={{ insetInlineStart: `${todayAt}%` }}
                />
                {barsFor(r, view.today).map((b, i, all) => {
                  const left = pos(b.from);
                  const width = Math.max(0.8, pos(b.to) - left);
                  const first = i === 0 || all[i - 1].to !== b.from;
                  const last = i === all.length - 1 || all[i + 1].from !== b.to;
                  return (
                    <div
                      key={i}
                      className={cls('absolute top-[3px] h-4', BAR_LOOK[b.look], first && 'rounded-s-full', last && 'rounded-e-full')}
                      style={{
                        insetInlineStart: `${left}%`,
                        width: `${width}%`,
                        ...(b.look === 'late' ? { background: 'repeating-linear-gradient(135deg, #A8321F 0 6px, #C9574A 6px 12px)' } : {}),
                      }}
                    />
                  );
                })}
              </div>
            </div>
          ))}
          {expectedRows.map(({ e, from }) => (
            <div
              key={e.title}
              className="grid grid-cols-1 items-center gap-2 border-b border-rule py-3.5 last:border-b-0 md:grid-cols-[230px_minmax(0,1fr)] md:gap-4"
            >
              <div>
                <p className="m-0 font-bold">{e.title}</p>
                <p className="m-0 text-[14px] text-ink-soft">{t('expectedBy', { date: dayMonth(e.date, locale) })}</p>
              </div>
              <div className="relative h-[22px]">
                <div
                  aria-hidden="true"
                  className="absolute -inset-y-1 z-[1] w-0.5 bg-ink md:hidden"
                  style={{ insetInlineStart: `${todayAt}%` }}
                />
                <div
                  className={cls('absolute top-[3px] h-4 rounded-full', BAR_LOOK.expected)}
                  style={{ insetInlineStart: `${pos(from)}%`, width: `${Math.max(1, pos(e.date) - pos(from))}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </Sheet>
  );
}

const LETTER_KINDS = ['reminder', 'chase', 'request', 'objection', 'grievance'] as const;
type LetterKind = (typeof LETTER_KINDS)[number];

function paragraphs(text: string) {
  return text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
}

function LetterCard({ lv, view }: { lv: LetterView; view: TrackerView }) {
  const t = useTranslations('tracker');
  const locale = useLocale();
  const { caseId, viewerId, readOnly } = useCase();
  const act = useLetterAction(caseId);
  const [copy, setCopy] = useState(false);
  const L = lv.letter;
  const kind = LETTER_KINDS.includes(L.kind as LetterKind)
    ? (L.kind as LetterKind)
    : /objection/i.test(L.title)
      ? 'objection'
      : /grievance|complaint/i.test(L.title)
        ? 'grievance'
        : 'reminder';
  const signer = lv.signer ? personName(lv.signer, locale) : '';
  const mirrorLang = L.letter_lang && L.letter_lang !== 'en' ? L.letter_lang : null;
  const text = copy && L.letter_mirror ? L.letter_mirror : (L.letter_text ?? '');
  const pdf = apiUrl(`/v1/cases/${caseId}/letters/${L.id}/file${copy ? '?mirror=1' : ''}`);
  return (
    <section aria-labelledby={`letter-${L.id}`} className="min-w-0 rounded-[14px] border border-rule bg-sheet px-6 py-[22px]">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id={`letter-${L.id}`} className="m-0 font-serif text-[24px] font-semibold">
          {t('letterTo', { kind: t(`kinds.${kind}`), institution: lv.institution })}
        </h2>
        <span className="text-[14px] text-ink-soft">
          {L.status === 'draft'
            ? L.on === view.today
              ? t('draftedFor', { name: signer })
              : t('draftedOn', { name: signer, date: dayMonth(L.on, locale) })
            : L.status === 'approved'
              ? t('approvedNote')
              : L.status === 'sent'
                ? t('sentNote', { date: longDate(L.on, locale), expected: L.expected_on ? longDate(L.expected_on, locale) : '—' })
                : null}
        </span>
      </div>
      <div
        lang={copy && mirrorLang ? mirrorLang : 'en'}
        className="mt-3.5 rounded-[10px] border border-rule bg-[#FCFDFC] px-[22px] py-5 font-serif text-[16px] leading-[1.65]"
      >
        {paragraphs(text).map((p, i) => (
          <p key={i} className={cls('m-0 whitespace-pre-line', i > 0 && 'mt-3', i === 1 && 'font-semibold')}>
            {p}
          </p>
        ))}
      </div>
      {lv.fidelity === 'pass' ? (
        <p className="m-0 mt-3 text-[15px] font-bold text-ledger">
          {t('fidelityOk')} {mirrorLang && signer ? t('copyChecked', { language: languageName(mirrorLang, locale), name: signer }) : null}
        </p>
      ) : lv.fidelity === 'fail' ? (
        <p className="m-0 mt-3 text-[15px] font-bold text-debit">{t('fidelityFail')}</p>
      ) : null}
      <div className="mt-4 flex flex-wrap gap-2.5">
        {L.status === 'draft' ? (
          <Button
            disabled={act.isPending || readOnly}
            onClick={() =>
              act.mutate({ corrId: L.id, action: 'approve', by: viewerId }, { onSuccess: () => toast.success(t('approvedNote')) })
            }
          >
            {t('approveSend')}
          </Button>
        ) : null}
        {L.status === 'approved' ? (
          <Button
            disabled={act.isPending || readOnly}
            onClick={() =>
              act.mutate(
                { corrId: L.id, action: 'sent', by: viewerId, channel: 'letter' },
                { onSuccess: () => toast.success(t('markedSent')) },
              )
            }
          >
            {t('markSent')}
          </Button>
        ) : null}
        <a
          href={pdf}
          target="_blank"
          rel="noreferrer"
          className="inline-flex min-h-12 items-center gap-2 rounded-[10px] border border-rule-strong bg-sheet px-[18px] font-bold text-ink no-underline hover:text-ink"
        >
          <Printer size={18} aria-hidden="true" />
          {t('print')}
        </a>
        {mirrorLang && L.letter_mirror ? (
          <Button variant="secondary" onClick={() => setCopy((v) => !v)}>
            {copy ? t('readOriginal') : t('readCopy', { language: languageName(mirrorLang, locale) })}
          </Button>
        ) : null}
        {L.cites.length ? <Why ids={L.cites} cites={view.cites} title={L.title} className="self-center text-[15px]" /> : null}
      </div>
    </section>
  );
}

function Escalations({ view }: { view: TrackerView }) {
  const t = useTranslations('tracker');
  const entries = Object.entries(view.escalation).filter(([, list]) => list.length);
  if (!entries.length) return null;
  return (
    <>
      {entries.map(([claimId, list]) => {
        const row = view.rows.find((r) => r.claim_id === claimId);
        return (
          <aside
            key={claimId}
            aria-labelledby={`esc-${claimId}`}
            className="min-w-0 flex-[2_1_300px] rounded-[14px] border border-rule bg-sheet px-6 py-[22px]"
          >
            <h2 id={`esc-${claimId}`} className={h2Class}>
              {t('ifNoAnswer', { institution: row?.institution ?? '' })}
            </h2>
            <ol className="m-0 mt-3 flex list-decimal flex-col gap-2.5 ps-[22px]">
              {list.map((e, i) => (
                <li key={i}>
                  {e.text}{' '}
                  {!e.verified ? (
                    <Chip tone="pencil" className="ms-1 align-middle text-[12.5px]">
                      {t('notVerified')}
                    </Chip>
                  ) : null}
                  {e.cites.length ? (
                    <>
                      {' '}
                      <Why ids={e.cites} cites={view.cites} title={t('ifNoAnswer', { institution: row?.institution ?? '' })} />
                    </>
                  ) : null}
                </li>
              ))}
            </ol>
            {list.some((e) => !e.verified) ? <p className="m-0 mt-3.5 text-[15px] text-ink-soft">{t('unverifiedNote')}</p> : null}
          </aside>
        );
      })}
    </>
  );
}

function ReplyForm({ view, onDone }: { view: TrackerView; onDone: () => void }) {
  const t = useTranslations('tracker');
  const tc = useTranslations('common');
  const { caseId } = useCase();
  const reply = useRecordReply(caseId);
  const claims = view.rows.filter((r) => r.status !== 'settled' && r.status !== 'closed');
  const [claimId, setClaimId] = useState(claims[0]?.claim_id ?? view.rows[0]?.claim_id ?? '');
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!claimId || !title.trim() || !text.trim()) return;
    const id = toast.loading(tc('working'));
    reply.mutate(
      { claimId, title: title.trim(), text: text.trim() },
      {
        onSuccess: () => {
          toast.success(t('replyDone'), { id });
          onDone();
        },
        onError: (err) => toast.error(err.message, { id }),
      },
    );
  }
  const field = 'rounded-[10px] border border-rule-strong bg-sheet px-3 text-[17px] font-normal';
  return (
    <form onSubmit={submit} className="flex flex-col gap-3 rounded-[14px] border border-rule bg-sheet p-5">
      <label className="flex flex-col gap-1 text-[15px] font-bold">
        {t('replyClaim')}
        <select value={claimId} onChange={(e) => setClaimId(e.target.value)} className={cls(field, 'min-h-11')}>
          {view.rows.map((r) => (
            <option key={r.claim_id} value={r.claim_id}>
              {r.title}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-[15px] font-bold">
        {t('replyTitle')}
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={t('replyTitleHint')}
          className={cls(field, 'min-h-11')}
        />
      </label>
      <label className="flex flex-col gap-1 text-[15px] font-bold">
        {t('replyText')}
        <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} className={cls(field, 'py-2.5')} />
      </label>
      <div className="flex gap-2.5">
        <Button type="submit" disabled={reply.isPending || !title.trim() || !text.trim()}>
          {t('recordReply')}
        </Button>
        <Button variant="secondary" onClick={onDone}>
          {tc('cancel')}
        </Button>
      </div>
    </form>
  );
}

function ShareDesk({ view }: { view: TrackerView }) {
  const t = useTranslations('tracker');
  const tc = useTranslations('common');
  const locale = useLocale();
  const { caseId, viewerId, readOnly } = useCase();
  const share = useShare(caseId);
  const [links, setLinks] = useState<Record<string, string>>({});
  const rows = view.rows.filter((r) => ['submitted', 'acknowledged', 'returned', 'escalated'].includes(r.status) && r.kind === 'claim');
  if (!rows.length) return null;
  return (
    <Sheet className="px-6 py-5" aria-labelledby="desk">
      <h2 id="desk" className={h2Class}>
        {t('deskTitle')}
      </h2>
      <p className="m-0 mt-1.5 text-[15px] text-ink-soft">{t('deskLede')}</p>
      <ul className="m-0 mt-3 list-none p-0">
        {rows.map((r) => {
          const link = links[r.claim_id];
          return (
            <li key={r.claim_id} className="flex flex-wrap items-center justify-between gap-3 border-t border-rule py-3">
              <span className="font-bold">{r.title}</span>
              {link ? (
                <span className="flex flex-wrap items-center gap-2">
                  <a href={link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-bold">
                    {t('openDesk')}
                    <ExternalLink size={14} aria-hidden="true" />
                  </a>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      void navigator.clipboard?.writeText(link).then(
                        () => toast.success(tc('copied')),
                        () => toast(link),
                      );
                    }}
                  >
                    <Copy size={14} aria-hidden="true" />
                    {tc('copy')}
                  </Button>
                </span>
              ) : (
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={share.isPending || readOnly}
                  onClick={() =>
                    share.mutate(
                      { claimId: r.claim_id, by: viewerId },
                      {
                        onSuccess: (out) => {
                          setLinks((m) => ({ ...m, [r.claim_id]: `${window.location.origin}/${locale}/desk/${out.token}` }));
                          toast.success(t('shared'));
                        },
                      },
                    )
                  }
                >
                  <Share2 size={14} aria-hidden="true" />
                  {t('share', { institution: r.institution })}
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    </Sheet>
  );
}

type EventKind = 'complete_set_confirmed' | 'settled' | 'returned' | 'submitted';

/** What the family can record for one claim, given where it stands. */
function eventsFor(row: ClockRow): EventKind[] {
  if (row.status === 'settled' || row.status === 'closed') return [];
  const notifiedOnly = Boolean(row.events?.notified) && !row.events?.submitted;
  if (notifiedOnly) return []; // waiting for the institution's answer: its reply is added with "Add a reply"
  if (row.status === 'returned') return ['submitted'];
  if (row.status === 'acknowledged') return ['settled', 'returned'];
  if (row.status === 'submitted') return ['complete_set_confirmed', 'settled', 'returned'];
  return [];
}

/** The letter worth drafting next: a reminder once a rule's time limit has run out, a follow-up while a first letter
 * waits for an answer, otherwise a request asking what the institution still needs. */
function letterFor(row: ClockRow): 'reminder' | 'chase' | 'request' | null {
  if (row.status === 'settled' || row.status === 'closed') return null;
  if (row.late) return 'reminder';
  if (row.events?.notified && !row.events?.submitted) return 'chase';
  if (['submitted', 'acknowledged', 'returned'].includes(row.status)) return 'request';
  return null;
}

function EventForm({ row, event, onDone }: { row: ClockRow; event: EventKind; onDone: () => void }) {
  const t = useTranslations('tracker');
  const tc = useTranslations('common');
  const { caseId, viewerId } = useCase();
  const record = useClaimEvent(caseId);
  const [on, setOn] = useState('');
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const today = new Date().toISOString().slice(0, 10);
  function submit(e: FormEvent) {
    e.preventDefault();
    const id = toast.loading(tc('working'));
    record.mutate(
      {
        claimId: row.claim_id,
        event,
        on: on || null,
        amount: event === 'settled' && amount.trim() ? amount.trim() : null,
        note: note.trim() || null,
        by: viewerId,
      },
      {
        onSuccess: () => {
          toast.success(t('ev.done'), { id });
          onDone();
        },
        onError: (err) => toast.error(err.message, { id }),
      },
    );
  }
  const field = 'rounded-[10px] border border-rule-strong bg-sheet px-3 text-[17px] font-normal';
  return (
    <form onSubmit={submit} className="mt-3 flex flex-col gap-3 rounded-[12px] border border-rule bg-paper p-4">
      <p className="m-0 font-bold">{t(`ev.ask.${event}`, { institution: row.institution || row.title })}</p>
      <label className="flex flex-col gap-1 text-[15px] font-bold">
        {t('ev.when')}
        <input type="date" value={on} max={today} onChange={(e) => setOn(e.target.value)} className={cls(field, 'min-h-11 max-w-[220px]')} />
        <span className="text-[14px] font-normal text-ink-soft">{t('ev.whenHint')}</span>
      </label>
      {event === 'settled' ? (
        <label className="flex flex-col gap-1 text-[15px] font-bold">
          {t('ev.amount')}
          <input
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder={t('ev.amountHint')}
            className={cls(field, 'min-h-11 max-w-[320px]')}
          />
        </label>
      ) : null}
      {event === 'returned' ? (
        <label className="flex flex-col gap-1 text-[15px] font-bold">
          {t('ev.note')}
          <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} className={cls(field, 'py-2.5')} />
        </label>
      ) : null}
      <div className="flex flex-wrap gap-2.5">
        <Button type="submit" disabled={record.isPending}>
          {t('ev.save')}
        </Button>
        <Button variant="secondary" onClick={onDone}>
          {tc('cancel')}
        </Button>
      </div>
    </form>
  );
}

function ClaimProgress({ view, onReply }: { view: TrackerView; onReply: () => void }) {
  const t = useTranslations('tracker');
  const { caseId, viewerId, readOnly } = useCase();
  const draft = useDraftLetter(caseId);
  const [open, setOpen] = useState<{ claim: string; event: EventKind } | null>(null);
  const rows = view.rows.filter((r) => r.status !== 'closed');
  if (!rows.length) return null;
  function draftLetter(row: ClockRow, kind: 'reminder' | 'chase' | 'request') {
    const id = toast.loading(t('drafting'));
    draft.mutate(
      { claimId: row.claim_id, kind, by: viewerId },
      {
        onSuccess: () => toast.success(t('drafted'), { id }),
        onError: (err) => toast.error(err.message, { id }),
      },
    );
  }
  return (
    <Sheet as="section" className="px-6 py-5" aria-labelledby="progress">
      <h2 id="progress" className={h2Class}>
        {t('progressTitle')}
      </h2>
      <p className="m-0 mt-1.5 text-[15px] text-ink-soft">{t('progressLede')}</p>
      <ul className="m-0 mt-3 list-none p-0">
        {rows.map((r) => {
          const events = eventsFor(r);
          const letter = letterFor(r);
          const waiting = Boolean(r.events?.notified) && !r.events?.submitted && r.status !== 'settled';
          return (
            <li key={r.claim_id} className="border-t border-rule py-3.5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="m-0 font-bold">{r.institution || r.title}</p>
                  <p className="m-0 text-[14px] text-ink-soft">
                    {r.institution ? `${r.title} · ` : ''}
                    <span className={cls(r.late ? 'font-bold text-debit' : r.status === 'settled' ? 'font-bold text-ledger' : '')}>
                      {r.status_text}
                    </span>
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {waiting ? (
                    <Button size="sm" variant="secondary" disabled={readOnly} onClick={onReply}>
                      {t('ev.theyReplied')}
                    </Button>
                  ) : null}
                  {events.map((ev) => (
                    <Button
                      key={ev}
                      size="sm"
                      variant={ev === 'settled' ? 'primary' : 'secondary'}
                      disabled={readOnly}
                      aria-expanded={open?.claim === r.claim_id && open.event === ev}
                      onClick={() =>
                        setOpen((cur) => (cur?.claim === r.claim_id && cur.event === ev ? null : { claim: r.claim_id, event: ev }))
                      }
                    >
                      {t(`ev.button.${ev === 'settled' && r.kind === 'debt' ? 'settledDebt' : ev}`)}
                    </Button>
                  ))}
                  {letter ? (
                    <Button size="sm" variant="secondary" disabled={readOnly || draft.isPending} onClick={() => draftLetter(r, letter)}>
                      {t(`draft.${letter}`)}
                    </Button>
                  ) : null}
                </div>
              </div>
              {open?.claim === r.claim_id ? <EventForm row={r} event={open.event} onDone={() => setOpen(null)} /> : null}
            </li>
          );
        })}
      </ul>
    </Sheet>
  );
}

function Received({ view }: { view: TrackerView }) {
  const t = useTranslations('tracker');
  const locale = useLocale();
  if (!view.received.length && !view.expected.length) return null;
  return (
    <Sheet className="px-6 py-5" aria-labelledby="received">
      <h2 id="received" className={h2Class}>
        {t('received')}
      </h2>
      <ul className="m-0 mt-3 list-none p-0">
        {view.received.map((lv) => {
          const L = lv.letter;
          return (
            <li key={L.id} className="grid grid-cols-[72px_minmax(0,1fr)] gap-3 border-t border-rule py-3">
              <span className="font-bold tabular">{dayMonth(L.on, locale)}</span>
              <div>
                <p className="m-0 font-bold">
                  <span
                    className={cls(
                      'me-2 rounded-full px-2 py-px text-[12.5px] font-bold',
                      L.direction === 'in' ? 'bg-blank-tint text-blank' : 'bg-chip text-ink-soft',
                    )}
                  >
                    {L.direction === 'in' ? t('in') : t('out')}
                  </span>
                  {L.title}
                </p>
                {L.summary ? <p className="m-0 mt-0.5 text-[15px] text-ink-soft">{L.summary}</p> : null}
                {L.objection ? <p className="m-0 mt-1 text-[15px] font-bold text-debit">{t('objection', { rule: L.objection })}</p> : null}
              </div>
            </li>
          );
        })}
        {view.expected.map((e, i) => (
          <li key={`x${i}`} className="grid grid-cols-[72px_minmax(0,1fr)] gap-3 border-t border-rule py-3">
            <span className="font-bold tabular text-ink-faint">{dayMonth(e.date, locale)}</span>
            <div>
              <p className="m-0 font-bold">
                <span className="me-2 rounded-full border-[1.5px] border-dotted border-blank px-2 py-px text-[12.5px] font-bold text-blank">
                  {t('expected')}
                </span>
                {e.title}
              </p>
              {e.note ? <p className="m-0 mt-0.5 text-[15px] text-ink-soft">{e.note}</p> : null}
            </div>
          </li>
        ))}
      </ul>
    </Sheet>
  );
}

export function TrackerScreen({ caseId }: { caseId: string }) {
  const t = useTranslations('tracker');
  const { readOnly } = useCase();
  const q = useTracker(caseId);
  const [replying, setReplying] = useState(false);
  const view = q.data;
  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className={h1Class}>{t('title')}</h1>
          <p className="m-0 mt-2 text-ink-soft">{t('lede')}</p>
        </div>
        <Button variant="secondary" aria-expanded={replying} disabled={readOnly} onClick={() => setReplying((v) => !v)}>
          {t('addReply')}
        </Button>
      </header>
      {!view ? (
        <Skeleton className="h-80" />
      ) : (
        <>
          {replying ? <ReplyForm view={view} onDone={() => setReplying(false)} /> : null}
          <Timeline view={view} />
          <ClaimProgress
            view={view}
            onReply={() => {
              setReplying(true);
              window.scrollTo({ top: 0, behavior: 'smooth' });
            }}
          />
          <div className="flex flex-wrap items-start gap-6">
            <div className="flex min-w-0 flex-[3_1_520px] flex-col gap-6">
              {view.letters.length ? (
                view.letters.map((lv) => <LetterCard key={lv.letter.id} lv={lv} view={view} />)
              ) : (
                <Sheet className="px-6 py-5">
                  <p className="m-0 text-ink-soft">{view.rows.length ? t('noLetters') : t('nothingYet')}</p>
                </Sheet>
              )}
            </div>
            <Escalations view={view} />
          </div>
          <Received view={view} />
          <ShareDesk view={view} />
        </>
      )}
    </div>
  );
}
