/**
 * The small pieces every screen is built from, matching the AfterLedger design canvas: the ledger logo,
 * buttons, certainty pills (line style carries the meaning), stamps for real lateness, initials avatars,
 * sheets and headings.
 */
import type { PersonChip } from '@afterledger/contracts';
import { useLocale, useTranslations } from 'next-intl';
import type { ComponentProps, ReactNode } from 'react';
import { Link } from '@/i18n/navigation';
import { cls, personName } from '@/lib/format';

export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 30 30" aria-hidden="true">
      <rect x="5" y="13" width="20" height="5.5" rx="1" fill="#FFE066" />
      <rect x="4" y="3" width="22" height="24" rx="3" fill="none" stroke="#14202B" strokeWidth="1.8" />
      <path d="M8 9.5h14M8 15.75h14M8 22h9" stroke="#14202B" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

export function Wordmark({ size = 23 }: { size?: number }) {
  return (
    <span className="font-serif font-semibold tracking-[-0.01em]" style={{ fontSize: size }}>
      AfterLedger
    </span>
  );
}

type Variant = 'primary' | 'secondary' | 'dark' | 'highlight' | 'quiet' | 'danger';

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-ledger text-white hover:bg-ledger-deep hover:text-white border border-ledger',
  secondary: 'bg-sheet text-ink border border-rule-strong hover:border-ink-soft hover:text-ink',
  dark: 'bg-ink text-white border border-ink hover:bg-[#22313e] hover:text-white',
  highlight: 'bg-hl text-ink border border-hl hover:brightness-95 hover:text-ink',
  quiet: 'bg-transparent text-ledger border border-transparent hover:text-ledger-deep underline-offset-4 hover:underline',
  danger: 'bg-debit text-white border border-debit hover:brightness-95 hover:text-white',
};

const SIZES = { md: 'min-h-[46px] px-[18px] text-[17px]', lg: 'min-h-[52px] px-[26px] text-[18px]', sm: 'min-h-[40px] px-3 text-[15px]' };

export function buttonClass(variant: Variant = 'primary', size: keyof typeof SIZES = 'md', extra?: string) {
  return cls(
    'inline-flex items-center justify-center gap-2 rounded-[10px] font-bold no-underline transition-colors',
    'disabled:cursor-not-allowed disabled:opacity-55',
    VARIANTS[variant],
    SIZES[size],
    extra,
  );
}

export function Button({
  variant = 'primary',
  size = 'md',
  className,
  ...rest
}: ComponentProps<'button'> & { variant?: Variant; size?: keyof typeof SIZES }) {
  return <button type="button" className={buttonClass(variant, size, className)} {...rest} />;
}

export function ButtonLink({
  variant = 'primary',
  size = 'md',
  className,
  ...rest
}: ComponentProps<typeof Link> & { variant?: Variant; size?: keyof typeof SIZES }) {
  return <Link className={buttonClass(variant, size, className)} {...rest} />;
}

export type Certainty = 'sure' | 'check' | 'unknown';

/** Sure (solid), Please check (dashed pencil), We don't know yet (dotted blank). */
export function CertaintyPill({ certainty, children, className }: { certainty: Certainty; children: ReactNode; className?: string }) {
  const look = {
    sure: 'bg-ledger-tint text-ledger border-[1.5px] border-solid border-ledger',
    check: 'bg-pencil-tint text-pencil border-[1.5px] border-dashed border-pencil',
    unknown: 'bg-sheet text-blank border-[1.5px] border-dotted border-blank',
  }[certainty];
  return (
    <span className={cls('inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[13px] font-bold', look, className)}>
      {children}
    </span>
  );
}

export function Chip({
  children,
  className,
  tone = 'plain',
}: {
  children: ReactNode;
  className?: string;
  tone?: 'plain' | 'ledger' | 'debit' | 'pencil' | 'blank' | 'night';
}) {
  const look = {
    plain: 'bg-chip text-ink-soft',
    ledger: 'bg-ledger-tint text-ledger border-[1.5px] border-ledger font-bold',
    debit: 'bg-debit-tint text-debit border-[1.5px] border-debit font-bold',
    pencil: 'bg-pencil-tint text-pencil border-[1.5px] border-dashed border-pencil font-bold',
    blank: 'bg-blank-tint text-blank font-bold',
    night: 'bg-white/10 text-night-soft',
  }[tone];
  return <span className={cls('inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[14px]', look, className)}>{children}</span>;
}

export function Stamp({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cls('stamp', className)}>{children}</span>;
}

const AVATAR_INK: Record<string, string> = {
  '#F3D9C4': '#6B3A12',
  '#CFE3F5': '#1F4466',
  '#E6DCF2': '#553A6E',
  '#D8EFD9': '#1F5131',
  '#F7E3B5': '#6B4A00',
  '#E2E8E4': '#33433A',
  '#F5D0D6': '#7A2232',
  '#DCE6F4': '#2C4470',
  '#F3E3C2': '#6B4A00',
  '#E8DDF0': '#553A6E',
  '#D9D2C5': '#4A4136',
};

export function Avatar({
  person,
  size = 28,
  className,
}: {
  person: Pick<PersonChip, 'initials' | 'color' | 'name'>;
  size?: number;
  className?: string;
}) {
  const bg = person.color || '#DCE6F4';
  return (
    <span
      aria-hidden="true"
      className={cls('inline-grid shrink-0 place-items-center rounded-full font-bold', className)}
      style={{
        width: size,
        height: size,
        background: bg,
        color: AVATAR_INK[bg.toUpperCase()] ?? '#2C4470',
        fontSize: Math.round(size * 0.46),
      }}
    >
      {person.initials || (person.name ? person.name.slice(0, 1) : '?')}
    </span>
  );
}

export function People({ people, label }: { people: PersonChip[]; label?: string }) {
  const locale = useLocale();
  if (!people.length) return null;
  return (
    <span className="inline-flex items-center gap-1.5 text-[15px] text-ink-soft">
      {people.map((p) => (
        <Avatar key={p.id} person={p} />
      ))}
      <span>{label ?? people.map((p) => personName(p, locale)).join(', ')}</span>
    </span>
  );
}

export function Sheet({
  as: Tag = 'section',
  className,
  children,
  ...rest
}: { as?: 'section' | 'div' | 'article' | 'aside' | 'figure'; className?: string; children: ReactNode } & Record<string, unknown>) {
  return (
    <Tag className={cls('rounded-[14px] border border-rule bg-sheet', className)} {...rest}>
      {children}
    </Tag>
  );
}

/** A wide table that scrolls sideways inside its box on a narrow screen. It is a named region that takes keyboard
 * focus, so it can be scrolled with the arrow keys as well as by touch. */
export function ScrollX({ label, className, children }: { label: string; className?: string; children: ReactNode }) {
  return (
    <div role="region" aria-label={label} tabIndex={0} className={cls('overflow-x-auto', className)}>
      {children}
    </div>
  );
}

export const h1Class = 'font-serif text-[30px] font-semibold leading-[1.12] tracking-[-0.015em] sm:text-[38px] m-0';
export const h2Class = 'font-serif text-[22px] font-semibold leading-snug m-0';
export const h2BigClass = 'font-serif text-[26px] font-semibold leading-snug m-0';
export const sectionTitleClass = 'm-0 text-[17px] font-bold';

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-ink-faint" role="status">
      <span className="h-3 w-3 animate-pulse-soft rounded-full bg-ledger" aria-hidden="true" />
      {label}
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cls('animate-pulse-soft rounded-[10px] bg-rule/60', className)} aria-hidden="true" />;
}

export function ErrorNote({ error, retry }: { error: unknown; retry?: () => void }) {
  const t = useTranslations('common');
  // a request that never reached the service (it is stopped, or a phone cannot reach the laptop) fails with the
  // browser's own words ("Failed to fetch"); the API's own refusals carry its explanation
  const msg = error instanceof TypeError ? t('unreachable') : error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="rounded-[14px] border border-debit bg-debit-tint p-4 text-[16px] text-debit">
      <p className="m-0 font-bold">{msg}</p>
      {retry ? (
        <button type="button" onClick={retry} className="tap mt-2 font-bold text-ledger underline">
          {t('tryAgain')}
        </button>
      ) : null}
    </div>
  );
}
