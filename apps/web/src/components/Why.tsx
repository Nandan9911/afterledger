'use client';
/**
 * "Why?" on any step opens the rule in its source's own words: the quote with a highlighter rule, the
 * source and paragraph, how well it is checked, and a link to the page it came from.
 */
import type { CiteView } from '@afterledger/contracts';
import { ExternalLink, X } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { cls, longDate, openable } from '@/lib/format';
import { Chip } from './ui';

export function levelTone(level: string): 'ledger' | 'pencil' | 'blank' | 'plain' {
  if (level === 'gold' || level === 'dual_verified' || level === 'adjudicated' || level === 'outcome_confirmed') return 'ledger';
  if (level === 'sourced') return 'pencil';
  return 'blank';
}

export function CiteCard({ cite, compact }: { cite: CiteView; compact?: boolean }) {
  const t = useTranslations('common');
  const locale = useLocale();
  const showTranslation = cite.quote_translation && cite.language.split('-')[0] !== locale;
  return (
    <article className={cls('flex flex-col gap-3', compact ? '' : 'border-t border-rule pt-4 first:border-t-0 first:pt-0')}>
      <p className="m-0 text-[17px] font-bold">{cite.text}</p>
      <blockquote lang={cite.language} className="m-0 border-s-[3px] border-hl py-1 ps-4 font-serif text-[17px] leading-[1.6]">
        “{cite.quote}”
      </blockquote>
      {showTranslation ? <p className="m-0 text-[15px] text-ink-soft">{cite.quote_translation}</p> : null}
      <p className="m-0 text-[15px] text-ink-soft">
        {cite.publisher && !cite.source_title.startsWith(cite.publisher) ? `${cite.publisher}, ` : ''}
        {cite.source_title}
        {cite.locator ? `, ${cite.locator}` : ''}.{' '}
        {openable(cite.read_url || cite.source_url) && !cite.fictional ? (
          <a
            href={cite.read_url || cite.source_url || undefined}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 font-bold"
          >
            {t('openPage')}
            <ExternalLink size={14} aria-hidden="true" />
          </a>
        ) : cite.source_url ? (
          <span className="font-bold text-blank">{t('samplePage')}</span>
        ) : null}
      </p>
      <div className="flex flex-wrap gap-2 text-[14px]">
        <Chip tone={levelTone(cite.level)}>{t(`level.${cite.level}` as 'level.gold')}</Chip>
        <Chip>{t(`tier.${cite.tier_label}` as 'tier.Regulator')}</Chip>
        {cite.checked_on ? <Chip>{t('checked', { date: longDate(cite.checked_on, locale) })}</Chip> : null}
        {!cite.fresh ? <Chip tone="pencil">{t('notFresh')}</Chip> : null}
      </div>
    </article>
  );
}

/** A "Why?" button that opens its sources in a side panel (a bottom sheet on a phone). The panel is rendered at the
 * end of the page only while it is open, so the button can sit inside a sentence. */
export function Why({
  ids,
  cites,
  title,
  label,
  className,
  children,
}: {
  ids: string[] | null | undefined;
  cites: Record<string, CiteView> | undefined;
  title?: string;
  label?: string;
  className?: string;
  children?: React.ReactNode;
}) {
  const t = useTranslations('common');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = (ids ?? []).map((i) => cites?.[i]).filter((c): c is CiteView => !!c);

  useEffect(() => {
    const d = ref.current;
    if (open && d && !d.open) d.showModal();
  }, [open]);

  function close() {
    setOpen(false);
    trigger.current?.focus();
  }

  if (!list.length) return null;
  return (
    <>
      <button
        ref={trigger}
        type="button"
        onClick={() => setOpen(true)}
        className={cls('tap font-bold text-ledger underline-offset-4 hover:underline', className ?? 'text-[15px]')}
      >
        {children ?? label ?? t('why')}
      </button>
      {open
        ? createPortal(
            <dialog
              ref={ref}
              onClose={close}
              onClick={(e) => {
                if (e.target === ref.current) close();
              }}
              className={cls(
                'm-0 max-h-[85vh] w-full max-w-none overflow-y-auto rounded-t-2xl border-[1.5px] border-ink bg-sheet p-0 text-ink backdrop:bg-ink/30',
                'fixed inset-x-0 bottom-0 top-auto sm:inset-y-0 sm:end-0 sm:start-auto sm:h-full sm:max-h-none sm:w-[460px] sm:rounded-none sm:rounded-s-2xl',
              )}
            >
              <div className="flex flex-col gap-4 p-6">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="m-0 font-serif text-[22px] font-semibold">{title ? t('whyTopic', { topic: title }) : t('why')}</h2>
                  <button
                    type="button"
                    aria-label={t('close')}
                    onClick={close}
                    className="grid h-11 w-11 shrink-0 place-items-center rounded-[10px] border border-rule-strong bg-sheet"
                  >
                    <X size={16} aria-hidden="true" />
                  </button>
                </div>
                {list.map((c) => (
                  <CiteCard key={c.id} cite={c} />
                ))}
              </div>
            </dialog>,
            document.body,
          )
        : null}
    </>
  );
}
