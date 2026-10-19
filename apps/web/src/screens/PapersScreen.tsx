'use client';
/**
 * Papers: every paper the family added, the page it was read from with a highlighter mark where each value
 * sits, and the values themselves. Values the two readings disagreed on are the only ones the family is
 * asked about (checkpoint C2). Also the empty state (what helps most) and the reading progress.
 */
import type { Checkpoint, PaperRow, PaperView, ValueView } from '@afterledger/contracts';
import { Camera, ChevronLeft, ChevronRight, ExternalLink, Trash2 } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useSearchParams } from 'next/navigation';
import { useRef, useState, type ReactNode } from 'react';
import { useCase } from '@/components/CaseShell';
import { Button, ErrorNote, h1Class, h2Class, Sheet, Skeleton, Spinner } from '@/components/ui';
import { usePathname, useRouter } from '@/i18n/navigation';
import { apiUrl } from '@/lib/api';
import { perform } from '@/lib/changes';
import { cls, dayMonth } from '@/lib/format';
import { useAnswer, useDeletePaper, useMoney, usePaper, usePapers, useUpload } from '@/lib/queries';

type Variant = 'primary' | 'secondary' | 'dark';

/** A button that opens the file chooser (or the camera) and adds what is chosen to the case. */
function AddPapers({
  variant = 'primary',
  photo = false,
  label,
  size = 'md',
  className,
}: {
  variant?: Variant;
  photo?: boolean;
  label: ReactNode;
  size?: 'md' | 'lg';
  className?: string;
}) {
  const t = useTranslations('papers');
  const { caseId, viewerId, readOnly } = useCase();
  const upload = useUpload(caseId);
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        hidden
        multiple={!photo}
        accept={photo ? 'image/*' : 'image/*,.heic,.heif,application/pdf'}
        capture={photo ? 'environment' : undefined}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = '';
          if (!files.length) return;
          perform(upload, { files, by: viewerId }, { working: t('uploading'), done: () => t('uploaded', { count: files.length }) });
        }}
      />
      <Button
        variant={variant}
        size={size}
        className={className}
        disabled={upload.isPending || readOnly}
        onClick={() => input.current?.click()}
      >
        {photo ? <Camera size={20} aria-hidden="true" /> : null}
        {label}
      </Button>
    </>
  );
}

function paperTitle(row: PaperRow, locale: string) {
  const p = row.paper;
  return {
    title: p.title,
    sub: p.title_en && p.title_en !== p.title && locale !== (p.languages[0] ?? '').split('-')[0] ? p.title_en : null,
  };
}

function PaperItem({ row, selected, onSelect }: { row: PaperRow; selected: boolean; onSelect: () => void }) {
  const t = useTranslations('papers');
  const locale = useLocale();
  const p = row.paper;
  const check = p.status === 'needs_check' || p.values_to_check > 0;
  const busy = p.status === 'queued' || p.status === 'reading';
  const { title, sub } = paperTitle(row, locale);
  const status = check
    ? t(p.origin === 'photo' ? 'photoToCheck' : 'valuesToCheck', { count: p.values_to_check || 1 })
    : busy
      ? p.stage || (p.status === 'queued' ? t('statusQueued') : t('statusReading'))
      : p.status === 'failed'
        ? t('statusFailed')
        : p.stage || t('statusDone');
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? 'true' : undefined}
        className={cls(
          'block w-full rounded-[10px] px-3 py-2.5 text-start',
          check
            ? 'border-[1.5px] border-dashed border-pencil bg-pencil-tint'
            : selected
              ? 'border-[1.5px] border-ink bg-sheet'
              : 'border border-rule bg-sheet hover:border-rule-strong',
        )}
      >
        <span className="block text-[15px] font-bold leading-snug" lang={p.languages[0]}>
          {title}
        </span>
        {sub ? <span className="block text-[13px] text-ink-faint">{sub}</span> : null}
        <span
          className={cls(
            'block text-[13px]',
            check ? 'font-bold text-pencil' : busy ? 'text-ink-soft' : p.status === 'failed' ? 'font-bold text-debit' : 'text-ledger',
          )}
        >
          {status}
        </span>
        {busy ? <span aria-hidden="true" className="scan mt-1.5 block h-1.5 rounded-full" /> : null}
      </button>
    </li>
  );
}

function CheckCard({ v, cp }: { v: ValueView; cp: Checkpoint }) {
  const t = useTranslations('papers');
  const tc = useTranslations('common');
  const locale = useLocale();
  const { caseId, viewerId, readOnly } = useCase();
  const answer = useAnswer(caseId);
  const [no, setNo] = useState(false);
  const [value, setValue] = useState('');
  const question = cp.question_local && cp.language.split('-')[0] === locale ? cp.question_local : cp.question;
  const done = () => t('thanks');
  return (
    <div className="mt-3.5 rounded-xl border-[1.5px] border-dashed border-pencil bg-pencil-tint p-4">
      <p className="m-0 text-[14px] font-bold text-pencil">{t('checkThis')}</p>
      <p className="m-0 mt-1 text-[15px] text-ink-soft">{v.value.label}</p>
      <p className="m-0 mt-0.5 text-[26px] font-bold tabular">{v.display || v.value.value}</p>
      {cp.why || v.value.note ? <p className="m-0 mt-2 text-[15px]">{cp.why || v.value.note}</p> : null}
      <p className="m-0 mt-3 font-bold">{question}</p>
      {no ? (
        <form
          className="mt-2 flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!value.trim()) return;
            perform(answer, { checkpointId: cp.id, answer: 'no', value: value.trim(), by: viewerId }, { done });
          }}
        >
          <label className="flex flex-col gap-1 text-[15px] font-bold">
            {t('whatDoesItSay')}
            <input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              inputMode={v.value.kind === 'amount' ? 'decimal' : 'text'}
              className="min-h-11 rounded-[10px] border border-rule-strong bg-sheet px-3 text-[18px] font-normal tabular"
            />
          </label>
          <div className="flex flex-wrap gap-2.5">
            <Button type="submit" variant="dark" disabled={answer.isPending || !value.trim()}>
              {tc('save')}
            </Button>
            <Button variant="secondary" onClick={() => setNo(false)}>
              {tc('cancel')}
            </Button>
          </div>
        </form>
      ) : (
        <div className="mt-2 flex flex-wrap gap-2.5">
          <Button
            variant="dark"
            disabled={answer.isPending || readOnly}
            onClick={() => perform(answer, { checkpointId: cp.id, answer: 'yes', by: viewerId }, { done })}
          >
            {tc('yes')}
          </Button>
          <Button variant="secondary" disabled={readOnly} onClick={() => setNo(true)}>
            {t('itSaysElse')}
          </Button>
        </div>
      )}
    </div>
  );
}

const AFTER = 'After the date of death.';

function ValueItem({ v, focused, onFocus }: { v: ValueView; focused: boolean; onFocus: (id: string | null) => void }) {
  const t = useTranslations('papers');
  const rv = v.value;
  const after = rv.note.startsWith(AFTER);
  const status =
    rv.status === 'family_confirmed'
      ? t('familyConfirmed')
      : rv.status === 'family_corrected'
        ? t('familyCorrected')
        : rv.status === 'one_path'
          ? t('onePath')
          : rv.status === 'disagree'
            ? t('checkThis')
            : rv.reading_b == null && rv.checks.includes('balance')
              ? t('sureBalance')
              : t('sureNote');
  return (
    <li
      className={cls('border-t border-rule py-3', focused && 'bg-hl/20')}
      onMouseEnter={() => onFocus(rv.id)}
      onMouseLeave={() => onFocus(null)}
      onFocus={() => onFocus(rv.id)}
      onBlur={() => onFocus(null)}
      tabIndex={0}
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[15px] text-ink-soft">{rv.label}</span>
        <span className="text-end font-bold tabular">{v.display || rv.value || '—'}</span>
      </div>
      {after ? (
        <p className="m-0 mt-1 text-[15px]">
          <span className="font-bold text-pencil">{t('afterDeath')}</span> {rv.note.slice(AFTER.length).trim()}
        </p>
      ) : (
        <>
          <p className={cls('m-0 mt-1 text-[14px] font-bold', rv.status === 'disagree' ? 'text-pencil' : 'text-ledger')}>{status}</p>
          {rv.note && rv.status !== 'matched' ? <p className="m-0 mt-0.5 text-[14px] text-ink-soft">{rv.note}</p> : null}
        </>
      )}
    </li>
  );
}

function onPage(v: ValueView, pageId: string, first: boolean) {
  if (v.value.page_id === pageId || v.value.evidence.some((e) => e.page_id === pageId)) return true;
  return first && !v.value.page_id && !v.value.evidence.some((e) => e.page_id);
}

function PageImage({ view, index, values, focus }: { view: PaperView; index: number; values: ValueView[]; focus: string | null }) {
  const page = view.pages[index];
  if (!page) return null;
  const marks = values.flatMap((v) =>
    v.value.evidence
      .filter((e) => e.page_id === page.id && e.box && e.box.length === 4)
      .slice(0, 1)
      .map((e) => ({ id: v.value.id, box: e.box as number[], check: v.checkpoint?.status === 'open' || v.value.certainty === 'check' })),
  );
  const photo = view.paper.origin === 'photo';
  return (
    <div
      className={cls(
        'relative overflow-hidden rounded-md bg-[#FBF8EF] shadow-[0_18px_30px_-18px_rgba(20,32,43,0.45)]',
        photo && '-rotate-[0.8deg]',
      )}
      style={{ aspectRatio: `${page.width} / ${page.height}` }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- page images come from the API, sized by their own aspect ratio */}
      <img src={apiUrl(page.image_url)} alt="" className="absolute inset-0 h-full w-full" />
      {marks.map((m, i) => (
        <span
          key={`${m.id}-${i}`}
          aria-hidden="true"
          className={cls(
            'absolute rounded-[2px] transition-[outline-width]',
            m.check
              ? 'outline-2 outline-offset-[-2px] outline-dashed outline-pencil'
              : 'bg-hl/45 outline-2 outline-[#C9A200] mix-blend-multiply',
            focus === m.id && 'outline-[3px]',
          )}
          style={{
            left: `${m.box[0] * 100}%`,
            top: `${m.box[1] * 100}%`,
            width: `${(m.box[2] - m.box[0]) * 100}%`,
            height: `${(m.box[3] - m.box[1]) * 100}%`,
          }}
        />
      ))}
    </div>
  );
}

function Viewer({ caseId, paperId, pageNo, setPage }: { caseId: string; paperId: string; pageNo: number; setPage: (n: number) => void }) {
  const t = useTranslations('papers');
  const tc = useTranslations('common');
  const locale = useLocale();
  const q = usePaper(caseId, paperId);
  const remove = useDeletePaper(caseId);
  const [focus, setFocus] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const view = q.data;
  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  if (!view || view.paper.id !== paperId) {
    return (
      <div className="flex flex-[999_1_620px] flex-wrap gap-5">
        <Skeleton className="h-[460px] flex-[3_1_460px]" />
        <Skeleton className="h-[460px] flex-[2_1_300px]" />
      </div>
    );
  }
  const pages = view.pages.length;
  const index = Math.min(Math.max(pageNo - 1, 0), Math.max(pages - 1, 0));
  const page = view.pages[index];
  const here = page ? view.values.filter((v) => onPage(v, page.id, index === 0)) : view.values;
  const checks = here.filter((v) => v.checkpoint?.status === 'open');
  const rest = here.filter((v) => v.checkpoint?.status !== 'open');
  // a statement's lines, read from this page (every line is checked against the running balance)
  const lines = page ? view.rows.filter((r) => r.page_id === page.id) : view.rows;
  const busy = view.paper.status === 'queued' || view.paper.status === 'reading';
  const removable = !view.paper.synthetic && !view.paper.fixture_key && !busy;
  return (
    <div className="flex min-w-0 flex-[999_1_620px] flex-wrap items-start gap-5">
      <section
        aria-label={t('viewer')}
        className="flex min-w-0 flex-[3_1_440px] flex-col gap-3 rounded-[14px] border border-rule bg-[#E9EEEA] p-4"
      >
        <div className="flex flex-wrap items-center justify-between gap-3 text-[15px]">
          <span className="font-bold" lang={view.paper.languages[0]}>
            {pages ? t('pageOf', { title: view.paper.title, page: index + 1, pages }) : view.paper.title}
          </span>
          <div className="flex flex-wrap gap-2">
            {pages > 1 ? (
              <>
                <button
                  type="button"
                  disabled={index === 0}
                  onClick={() => setPage(index)}
                  className="inline-flex min-h-10 items-center gap-1 rounded-lg border border-rule-strong bg-sheet px-3 disabled:opacity-50"
                >
                  <ChevronLeft size={16} aria-hidden="true" className="rtl:rotate-180" />
                  {t('prevPage')}
                </button>
                <button
                  type="button"
                  disabled={index >= pages - 1}
                  onClick={() => setPage(index + 2)}
                  className="inline-flex min-h-10 items-center gap-1 rounded-lg border border-rule-strong bg-sheet px-3 disabled:opacity-50"
                >
                  {t('nextPage')}
                  <ChevronRight size={16} aria-hidden="true" className="rtl:rotate-180" />
                </button>
              </>
            ) : null}
            {view.file_url ? (
              <a
                href={apiUrl(view.file_url)}
                target="_blank"
                rel="noreferrer"
                className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-rule-strong bg-sheet px-3 font-bold text-ink no-underline hover:text-ink"
              >
                {t('openOriginal')}
                <ExternalLink size={14} aria-hidden="true" />
              </a>
            ) : null}
            {removable && !confirming ? (
              <button
                type="button"
                onClick={() => setConfirming(true)}
                className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-rule-strong bg-sheet px-3 font-bold text-pencil"
              >
                <Trash2 size={15} aria-hidden="true" />
                {t('remove')}
              </button>
            ) : null}
          </div>
        </div>
        {removable && confirming ? (
          <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg border border-pencil bg-sheet px-4 py-3">
            <p className="m-0 min-w-0 flex-[1_1_260px] text-[15px]">{t('removeAsk', { title: view.paper.title })}</p>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                disabled={remove.isPending}
                onClick={() =>
                  perform(remove, view.paper.id, {
                    done: () => (setConfirming(false), t('removed', { title: view.paper.title })),
                  })
                }
              >
                {remove.isPending ? t('removing') : t('removeYes')}
              </Button>
              <Button variant="secondary" disabled={remove.isPending} onClick={() => setConfirming(false)}>
                {t('removeNo')}
              </Button>
            </div>
          </div>
        ) : null}
        {busy && !pages ? (
          <div className="grid h-72 place-items-center rounded-md bg-sheet">
            <Spinner label={view.paper.stage || tc('working')} />
          </div>
        ) : (
          <PageImage view={view} index={index} values={here} focus={focus} />
        )}
        <p className="m-0 text-[14px] text-ink-soft">{view.paper.synthetic ? `${t('marksNote')} ${t('syntheticNote')}` : t('marksNote')}</p>
      </section>

      <aside aria-labelledby="read-values" className="min-w-0 flex-[2_1_320px] rounded-[14px] border border-rule bg-sheet p-5">
        <h2 id="read-values" className={h2Class}>
          {t('readFrom')}
        </h2>
        {checks.map((v) => (
          <CheckCard key={v.value.id} v={v} cp={v.checkpoint!} />
        ))}
        {rest.length ? (
          <ul className="m-0 mt-4 list-none p-0">
            {rest.map((v) => (
              <ValueItem key={v.value.id} v={v} focused={focus === v.value.id} onFocus={setFocus} />
            ))}
          </ul>
        ) : !checks.length && !lines.length ? (
          <p className="m-0 mt-3 text-ink-soft">{busy ? view.paper.stage || tc('working') : t('nothingHere')}</p>
        ) : null}
        {lines.length ? (
          <section aria-labelledby="page-lines" className="mt-5">
            <h3 id="page-lines" className="m-0 text-[16px] font-bold">
              {t('linesOnPage', { count: lines.length })}
            </h3>
            <p className="m-0 mt-1 text-[14px] text-ink-faint">{t('linesNote')}</p>
            <ul className="m-0 mt-2 list-none p-0 text-[14.5px]">
              {lines.map((r) => (
                <li key={r.id} className="grid grid-cols-[auto_minmax(0,1fr)_auto] gap-x-3 border-t border-rule py-2">
                  <span className="tabular whitespace-nowrap text-ink-faint">{dayMonth(r.date, locale)}</span>
                  <span className="min-w-0 break-words" title={r.narration}>
                    {r.narration}
                  </span>
                  <span className={cls('tabular whitespace-nowrap text-end font-bold', r.direction === 'credit' && 'text-ledger')}>
                    {r.direction === 'credit' ? '+' : '−'}
                    {r.amount.text}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </aside>
    </div>
  );
}

function EmptyPapers() {
  const t = useTranslations('papers');
  const { header } = useCase();
  return (
    <div className="mx-auto flex max-w-[560px] flex-col gap-5 py-4">
      {header ? <p className="m-0 text-[15px] text-ink-faint">{t('caseOf', { name: header.case.title })}</p> : null}
      <svg width="132" height="104" viewBox="0 0 132 104" aria-hidden="true">
        <rect x="18" y="14" width="64" height="82" rx="6" fill="#FFFFFF" stroke="#A9C3B1" strokeWidth="2" transform="rotate(-6 50 55)" />
        <rect x="46" y="8" width="64" height="82" rx="6" fill="#FFFFFF" stroke="#14202B" strokeWidth="2" />
        <rect x="54" y="38" width="48" height="9" rx="2" fill="#FFE066" />
        <path d="M56 24h40M56 42h40M56 60h40M56 74h26" stroke="#14202B" strokeWidth="2" strokeLinecap="round" />
      </svg>
      <div>
        <h1 className={h1Class}>{t('emptyTitle')}</h1>
        <p className="m-0 mt-2 text-[18px] text-ink-soft">{t('emptyText')}</p>
      </div>
      <Sheet className="px-5 py-4">
        <h2 className="m-0 text-[17px] font-bold">{t('helpMost')}</h2>
        <ul className="m-0 mt-2 flex flex-col gap-1 ps-5">
          <li>{t('help1')}</li>
          <li>{t('help2')}</li>
          <li>{t('help3')}</li>
          <li>{t('help4')}</li>
        </ul>
        <p className="m-0 mt-3 text-[15px] text-ink-faint">{t('noneRequired')}</p>
      </Sheet>
      <div className="flex flex-col gap-2.5 sm:flex-row">
        <AddPapers photo label={t('takePhoto')} size="lg" className="sm:flex-1" />
        <AddPapers variant="secondary" label={t('chooseFiles')} size="lg" className="sm:flex-1" />
      </div>
    </div>
  );
}

function Reading({ rows }: { rows: PaperRow[] }) {
  const t = useTranslations('papers');
  const { caseId } = useCase();
  const money = useMoney(caseId);
  const busy = rows.filter((r) => r.paper.status === 'queued' || r.paper.status === 'reading');
  const done = rows.length - busy.length;
  const share = rows.length ? Math.round((done / rows.length) * 100) : 0;
  const found = (money.data?.countries ?? [])
    .flatMap((c) =>
      c.institutions.flatMap((g) => g.holdings.map((h) => `${h.holding.label}${g.name ? `, ${g.short_name ?? g.name}` : ''}`)),
    )
    .slice(0, 5);
  return (
    <Sheet className="flex flex-col gap-3 px-5 py-4" aria-live="polite">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className={h2Class}>{t('readingTitle', { count: busy.length })}</h2>
        <p className="m-0 text-[15px] text-ink-soft">{t('closeNote')}</p>
      </div>
      <div
        role="progressbar"
        aria-valuenow={share}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={t('progress')}
        className="h-2.5 overflow-hidden rounded-full bg-rule"
      >
        <div className="h-full bg-ledger transition-[width]" style={{ width: `${share}%` }} />
      </div>
      {found.length ? (
        <section className="rounded-xl bg-ledger-tint px-3 py-2.5">
          <h3 className="m-0 text-[15px] font-bold text-ledger">{t('foundSoFar')}</h3>
          <ul className="m-0 mt-1 ps-5 text-[15px]">
            {found.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </Sheet>
  );
}

export function PapersScreen({ caseId }: { caseId: string }) {
  const t = useTranslations('papers');
  const q = usePapers(caseId);
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const rows = q.data?.papers ?? [];
  const wanted = params.get('paper');
  const fallback =
    rows.find((r) => r.paper.values_to_check > 0) ??
    rows.find((r) => r.paper.status === 'read' || r.paper.status === 'needs_check') ??
    rows[0];
  const selected = rows.find((r) => r.paper.id === wanted) ?? fallback;
  const pageNo = Number(params.get('page') ?? '1') || 1;

  function go(paperId: string, page = 1) {
    router.replace({ pathname, query: { paper: paperId, ...(page > 1 ? { page: String(page) } : {}) } }, { scroll: false });
  }

  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  if (!q.data) {
    return (
      <div className="flex flex-col gap-5">
        <Skeleton className="h-12 w-60" />
        <Skeleton className="h-[480px]" />
      </div>
    );
  }
  if (!rows.length) return <EmptyPapers />;

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className={h1Class}>{t('title')}</h1>
          <p className="m-0 mt-1.5 text-ink-soft">{t('lede')}</p>
        </div>
        <AddPapers label={t('add')} photo={false} />
      </header>
      {q.data.reading ? <Reading rows={rows} /> : null}
      <div className="flex flex-wrap items-start gap-5">
        <ul
          aria-label={t('allPapers')}
          className="m-0 flex w-full list-none flex-col gap-1.5 p-0 sm:w-auto sm:max-w-[260px] sm:flex-[1_1_230px]"
        >
          {rows.map((r) => (
            <PaperItem key={r.paper.id} row={r} selected={r.paper.id === selected?.paper.id} onSelect={() => go(r.paper.id)} />
          ))}
        </ul>
        {selected ? (
          <Viewer
            key={selected.paper.id}
            caseId={caseId}
            paperId={selected.paper.id}
            pageNo={wanted === selected.paper.id || !wanted ? pageNo : 1}
            setPage={(n) => go(selected.paper.id, n)}
          />
        ) : null}
      </div>
    </div>
  );
}
