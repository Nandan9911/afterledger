'use client';
/**
 * The frame around every case screen: on a wide screen a ledger-style sidebar with the case, the
 * navigation and its counts; on a phone a short header and five tabs. It also keeps the case live
 * (server-sent events refresh what is open) and remembers who is viewing.
 */
import type { CaseHeader, NavCounts } from '@afterledger/contracts';
import { Banknote, CircleEllipsis, FileText, Home, ListChecks, Mic } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useSearchParams } from 'next/navigation';
import { useIsFetching } from '@tanstack/react-query';
import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Link, usePathname, useRouter } from '@/i18n/navigation';
import { localeNames, locales, type Locale } from '@/i18n/routing';
import { ApiError } from '@/lib/api';
import { useCaseEvents } from '@/lib/events';
import { cls, countryName, languageName, longDate, personName } from '@/lib/format';
import { rememberCase } from '@/lib/mycases';
import { useCaseHeader, useConversation, useRefreshToday, useResetDemo } from '@/lib/queries';
import { useSite } from '@/lib/site';
import { useViewer, useViewerStore } from '@/lib/store';
import { Button, ErrorNote, Logo, Skeleton, SlowNote } from './ui';
import { WatchBanner } from './WatchBanner';
import { SignInFirst } from './Account';
import { useAuth } from '@/lib/auth';

/** Puts a demo case back as it stands today (from its snapshot, in seconds, with no model calls), after asking:
 * whatever was tried on it is undone. */
function ResetDemo({ demoKey }: { demoKey: string }) {
  const t = useTranslations('nav');
  const tc = useTranslations('common');
  const reset = useResetDemo();
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <button
        type="button"
        onClick={() => setAsking(true)}
        className="tap self-center text-[14px] font-bold text-ink-soft underline-offset-4 hover:underline"
      >
        {t('resetCase')}
      </button>
    );
  }
  return (
    <div
      role="group"
      aria-label={t('resetCase')}
      className="flex flex-col gap-2.5 rounded-xl border border-rule-strong bg-paper p-3 text-[14px]"
    >
      <p className="m-0">{t('resetAsk')}</p>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="danger"
          disabled={reset.isPending}
          onClick={() =>
            reset.mutate(demoKey, {
              onSuccess: () => {
                setAsking(false);
                toast.success(t('resetDone'));
              },
              onError: (e) => {
                setAsking(false);
                toast.error(e.message);
              },
            })
          }
        >
          {t('resetYes')}
        </Button>
        <Button size="sm" variant="secondary" disabled={reset.isPending} onClick={() => setAsking(false)}>
          {tc('cancel')}
        </Button>
      </div>
    </div>
  );
}

type Ctx = { caseId: string; header: CaseHeader | undefined; viewerId: string | null; readOnly: boolean };
const CaseContext = createContext<Ctx>({ caseId: '', header: undefined, viewerId: null, readOnly: false });
export const useCase = () => useContext(CaseContext);

type NavKey = 'today' | 'story' | 'papers' | 'money' | 'leads' | 'people' | 'plans' | 'packs' | 'tracker' | 'sources' | 'activity';

const ORDER: NavKey[] = ['today', 'papers', 'money', 'leads', 'people', 'plans', 'packs', 'tracker', 'sources', 'activity'];

function badge(key: NavKey, nav: NavCounts | undefined, t: ReturnType<typeof useTranslations>) {
  if (!nav) return null;
  const faint = 'text-ink-faint text-[14px]';
  switch (key) {
    case 'papers':
      return nav.papers_to_check ? (
        <span className="text-[14px] font-bold text-pencil">{t('toCheck', { count: nav.papers_to_check })}</span>
      ) : nav.papers ? (
        <span className={faint}>{nav.papers}</span>
      ) : null;
    case 'money':
      return nav.money ? <span className={faint}>{nav.money}</span> : null;
    case 'leads':
      return nav.leads_open ? <span className="text-[14px] font-bold text-pencil">{t('open', { count: nav.leads_open })}</span> : null;
    case 'people':
      return nav.people_questions ? (
        <span className="text-[14px] font-bold text-pencil">{t('questions', { count: nav.people_questions })}</span>
      ) : nav.people ? (
        <span className={faint}>{nav.people}</span>
      ) : null;
    case 'plans':
      return nav.plan_questions ? (
        <span className="text-[14px] font-bold text-pencil">{t('questions', { count: nav.plan_questions })}</span>
      ) : nav.plans ? (
        <span className={faint}>{nav.plans}</span>
      ) : null;
    case 'packs':
      return nav.packs ? <span className={nav.packs_ready ? 'text-[14px] font-bold text-ledger' : faint}>{nav.packs}</span> : null;
    case 'tracker':
      return nav.tracker_late ? <span className="text-[14px] font-bold text-debit">{t('late', { count: nav.tracker_late })}</span> : null;
    case 'sources':
      return nav.sources ? <span className={faint}>{nav.sources}</span> : null;
    default:
      return null;
  }
}

export function CaseShell({ caseId, children }: { caseId: string; children: React.ReactNode }) {
  const t = useTranslations('nav');
  const tc = useTranslations('common');
  const tp = useTranslations('people');
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const stored = useViewer(caseId);
  const setViewer = useViewerStore((s) => s.setViewer);
  const header = useCaseHeader(caseId, stored);
  // the story page waits for the case before it is drawn (so the layout never moves under someone typing); its
  // conversation is asked for at the same time, so the wait adds no round trip
  useConversation(caseId, pathname.split('/')[3] === 'story');
  const refreshToday = useRefreshToday(caseId);
  const { live } = useCaseEvents(caseId);
  const [moreAt, setMoreAt] = useState<string | null>(null);
  const params = useSearchParams();
  const site = useSite();
  const signedIn = useAuth((s) => s.user !== null);
  const isAdmin = useAuth((s) => s.user?.role === 'admin');
  // where sign-in is required, only the admin may put a shared sample case back as it was built
  const mayReset = !site.auth || isAdmin;
  const tAuth = useTranslations('auth');
  const h = header.data;
  const demoKey = h?.case.demo_key ?? (caseId.startsWith('demo-') ? caseId.slice(5) : null);
  const openedOwn = Boolean(h && !h.case.demo_key);
  useEffect(() => {
    if (openedOwn) rememberCase(caseId); // so this browser's home page lists it
  }, [openedOwn, caseId]);
  const watching = params.get('watch') === '1' && !!demoKey;
  const viewerId = stored ?? h?.viewer?.id ?? null;
  const active = (pathname.split('/')[3] as NavKey | undefined) ?? 'today';

  const more = moreAt === pathname; // the More sheet closes by itself when the page changes
  // What prepared this case's content: said plainly, so scripted responses are never taken for a live run.
  const modeLabel = (mode: string) => (mode === 'gemini' ? tc('modeGemini') : mode === 'replay' ? tc('modeReplay') : tc('modeScripted'));

  // a shared sample on the public site: every page opens, its buttons are switched off, questions are answered
  const readOnly = Boolean(h?.read_only);
  const ctx = useMemo(() => ({ caseId, header: h, viewerId, readOnly }), [caseId, h, viewerId, readOnly]);

  const href = (key: NavKey) => `/cases/${caseId}/${key}`;
  const deathCountry = h?.case.situation?.death_country ?? h?.case.country_codes?.[0] ?? null;
  const place = [
    h?.case.died_in,
    deathCountry && h?.case.died_in && !h.case.died_in.includes(countryName(deathCountry, locale))
      ? countryName(deathCountry, locale)
      : null,
  ]
    .filter(Boolean)
    .join(', ');
  const died = h?.case.died_on
    ? place
      ? t('died', { date: longDate(h.case.died_on, locale), place })
      : t('diedNoPlace', { date: longDate(h.case.died_on, locale) })
    : null;

  function chooseViewer(personId: string) {
    setViewer(caseId, personId);
    if (!readOnly) refreshToday.mutate(personId); // a sample keeps the greetings it has
    const person = h?.family.find((p) => p.id === personId);
    const lang = person?.languages?.[0]?.split('-')[0];
    if (lang && (locales as readonly string[]).includes(lang) && lang !== locale) {
      router.replace(pathname, { locale: lang as Locale });
    }
  }

  // A case still being told has nothing else to show yet: its story page takes the whole window. Until the case
  // says which it is, the story page waits, so it is not drawn in one frame and then moved to the other (which
  // threw away whatever the person had started typing).
  if (active === 'story' && !h && !header.isError) {
    return (
      <div className="flex min-h-screen flex-col gap-4 bg-paper p-8" aria-busy="true">
        <Skeleton className="h-10 w-80" />
        <Skeleton className="h-24 max-w-[760px]" />
      </div>
    );
  }
  if (active === 'story' && h?.case.stage === 'intake') {
    return <CaseContext.Provider value={ctx}>{children}</CaseContext.Provider>;
  }

  if (header.isError && !watching) {
    // the case was not found for this visitor (or is another account's): sign in, or say so; anything else (the
    // service busy or out of reach) is said as it is, with a way to try again, never as a request to sign in
    const missing = header.error instanceof ApiError && [401, 403, 404].includes(header.error.status);
    return (
      <div className="mx-auto flex max-w-xl flex-col gap-4 p-8">
        {missing && site.auth && !signedIn ? (
          // a case of one's own opens only when signed in to its account
          <SignInFirst next={pathname} title={tAuth('signIn')} />
        ) : missing ? (
          <ErrorNote error={new Error(t('caseMissing'))} />
        ) : (
          <ErrorNote error={header.error} retry={() => header.refetch()} />
        )}
        <p className="mt-4">
          <Link href="/">{tc('home')}</Link>
        </p>
      </div>
    );
  }

  const viewer = h?.family.find((p) => p.id === viewerId) ?? h?.viewer ?? null;
  const viewerLang = languageName(locale, locale) || localeNames[locale as Locale] || locale;

  const caseCard = (
    <div className="rounded-xl border border-rule bg-paper p-3">
      {h ? (
        <>
          <p className="m-0 font-serif text-[18px] font-semibold leading-tight">{h.case.title}</p>
          {h.case.title_en && h.case.title_en !== h.case.title ? <p className="m-0 text-[14px] text-ink-faint">{h.case.title_en}</p> : null}
          {died ? <p className="m-0 mt-1 text-[14px] text-ink-soft">{died}</p> : null}
          {h.case.synthetic ? <p className="m-0 mt-2 text-[13px] font-bold text-blank">{tc('synthetic')}</p> : null}
          <Link
            href={href('story')}
            aria-current={active === 'story' ? 'page' : undefined}
            className="mt-1.5 inline-block text-[14px] font-bold"
          >
            {t('storyLink')}
          </Link>
        </>
      ) : (
        <Skeleton className="h-16" />
      )}
    </div>
  );

  const navList = (
    <ul className="m-0 flex list-none flex-col gap-0.5 p-0 text-[16px]">
      {ORDER.map((key) => {
        const on = active === key;
        return (
          <li key={key}>
            <Link
              href={href(key)}
              aria-current={on ? 'page' : undefined}
              className={cls(
                'flex min-h-[40px] items-center justify-between rounded-[10px] px-3 no-underline',
                on ? 'bg-ledger-tint font-bold text-ledger-deep' : 'text-ink hover:bg-paper hover:text-ink',
              )}
            >
              <span>{t(key)}</span>
              {badge(key, h?.nav, t)}
            </Link>
          </li>
        );
      })}
    </ul>
  );

  const viewerControl =
    h && h.family.length ? (
      <label className="flex flex-col gap-1 text-center text-[14px] text-ink-faint">
        <span>{viewer ? tc('viewingAs', { name: personName(viewer, locale), language: viewerLang }) : tc('viewAs')}</span>
        <select
          aria-label={tc('viewAs')}
          className="min-h-[40px] rounded-lg border border-rule-strong bg-sheet px-2 text-[15px] text-ink"
          value={viewerId ?? ''}
          onChange={(e) => chooseViewer(e.target.value)}
        >
          {h.family.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
              {p.relationship
                ? ` (${tp.has(`rel.${p.relationship}` as 'rel.wife') ? tp(`rel.${p.relationship}` as 'rel.wife') : p.relationship})`
                : ''}
            </option>
          ))}
        </select>
      </label>
    ) : null;

  return (
    <CaseContext.Provider value={ctx}>
      <div className="flex min-h-screen bg-paper">
        {/* ---- the sidebar, on wide screens ---- */}
        <nav aria-label={t('case')} className="hidden w-[270px] shrink-0 border-e border-rule bg-sheet lg:block">
          <div className="sticky top-0 flex h-screen flex-col gap-4 overflow-y-auto px-3.5 pb-5 pt-4">
            <Link href="/" className="flex items-center gap-2.5 px-2 text-ink no-underline">
              <Logo size={26} />
              <span className="font-serif text-[20px] font-semibold">AfterLedger</span>
            </Link>
            {caseCard}
            {navList}
            <div className="mt-auto flex flex-col gap-2.5">
              <Link
                href={`/cases/${caseId}/ask`}
                className="flex min-h-[48px] items-center justify-center gap-2 rounded-xl bg-ink font-bold text-white no-underline hover:bg-[#22313e] hover:text-white"
              >
                <Mic size={18} aria-hidden="true" />
                {t('ask')}
              </Link>
              {viewerControl}
              <LanguageSelect compact />
              {h?.case.demo_key && mayReset ? <ResetDemo demoKey={h.case.demo_key} /> : null}
              <div className="text-center text-[12.5px] text-ink-faint">
                {h && !site.product ? <p className="m-0 font-bold">{modeLabel(h.mode)}</p> : null}
                <p className="m-0" role="status">
                  <span
                    className={cls('me-1 inline-block h-2 w-2 rounded-full', live ? 'bg-ledger' : 'bg-rule-strong')}
                    aria-hidden="true"
                  />
                  {live ? tc('updatesOn') : tc('updatesOff')}
                </p>
              </div>
            </div>
          </div>
        </nav>

        <div className="flex min-w-0 flex-1 flex-col">
          {/* ---- the phone header ---- */}
          <header className="flex items-center justify-between gap-3 border-b border-rule bg-sheet px-4 py-3 lg:hidden">
            <Link href="/" className="-m-2.5 flex items-center gap-2 p-2.5 text-ink no-underline" aria-label="AfterLedger">
              <Logo size={24} />
            </Link>
            <div className="min-w-0 flex-1">
              <p dir="auto" className="m-0 truncate text-[15px] font-bold">{h?.case.title ?? ''}</p>
              {h?.case.synthetic ? <p className="m-0 text-[12px] font-bold text-blank">{tc('synthetic')}</p> : null}
            </div>
            <Link
              href={`/cases/${caseId}/ask`}
              aria-label={t('ask')}
              className="grid h-11 w-11 place-items-center rounded-xl bg-ink text-white"
            >
              <Mic size={18} aria-hidden="true" />
            </Link>
          </header>

          <main id="main" className="min-w-0 flex-1 px-4 pb-28 pt-5 sm:px-8 lg:px-11 lg:pb-14 lg:pt-8">
            {watching && demoKey ? <WatchBanner demoKey={demoKey} /> : null}
            {readOnly ? <SampleBanner /> : null}
            <LoadingNote caseId={caseId} />
            {h?.working ? <WorkingBanner step={h.working} /> : null}
            {h?.researching?.length ? <ResearchBanner items={h.researching} /> : null}
            {children}
          </main>

          {/* ---- the phone tabs ---- */}
          <nav
            aria-label={t('main')}
            className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-rule bg-sheet px-1 pb-2.5 pt-1.5 lg:hidden"
          >
            {(
              [
                ['today', Home, t('today')],
                ['papers', FileText, t('papers')],
                ['money', Banknote, t('moneyShort')],
                ['plans', ListChecks, t('plansShort')],
              ] as const
            ).map(([key, Icon, label]) => (
              <Link
                key={key}
                href={href(key)}
                aria-current={active === key ? 'page' : undefined}
                className={cls(
                  'flex min-h-[52px] flex-col items-center justify-center gap-0.5 text-[13px] no-underline',
                  active === key ? 'font-bold text-ledger' : 'text-ink-soft',
                )}
              >
                <Icon size={22} aria-hidden="true" />
                {label}
              </Link>
            ))}
            <button
              type="button"
              onClick={() => setMoreAt(more ? null : pathname)}
              aria-expanded={more}
              className={cls(
                'flex min-h-[52px] flex-col items-center justify-center gap-0.5 text-[13px]',
                more ? 'font-bold text-ledger' : 'text-ink-soft',
              )}
            >
              <CircleEllipsis size={22} aria-hidden="true" />
              {t('more')}
            </button>
          </nav>
          {more ? (
            <div className="fixed inset-x-0 bottom-[70px] z-30 max-h-[70vh] overflow-y-auto rounded-t-2xl border border-rule bg-sheet p-4 shadow-[0_-20px_40px_-24px_rgba(20,32,43,0.4)] lg:hidden">
              {caseCard}
              <div className="mt-3">{navList}</div>
              <div className="mt-3 flex flex-col gap-3">
                {viewerControl}
                <LanguageSelect />
                {h?.case.demo_key && mayReset ? <ResetDemo demoKey={h.case.demo_key} /> : null}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </CaseContext.Provider>
  );
}

export function LanguageSelect({ compact, dark }: { compact?: boolean; dark?: boolean }) {
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const t = useTranslations('common');
  return (
    <label
      className={cls(
        'flex items-center gap-2',
        compact ? 'justify-center text-[14px]' : 'text-[15px]',
        dark ? 'text-night-soft' : 'text-ink-soft',
      )}
    >
      <span>{t('language')}</span>
      <select
        className="min-h-[40px] rounded-lg border border-rule-strong bg-sheet px-2.5 text-[15px] text-ink"
        value={locale}
        onChange={(e) => router.replace(pathname, { locale: e.target.value as Locale })}
      >
        {locales.map((l) => (
          <option key={l} value={l} lang={l}>
            {localeNames[l]}
          </option>
        ))}
      </select>
    </label>
  );
}

/** A shared sample case on the public site: what can be done here, and where to try the buttons. */
function SampleBanner() {
  const t = useTranslations('nav');
  return (
    <div role="note" className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-[12px] border border-blank bg-blank-tint px-4 py-3">
      <p className="m-0 min-w-0 flex-1 text-[15px]">
        <span className="font-bold">{t('sampleTitle')}</span> <span className="text-ink-soft">{t('sampleNote')}</span>
      </p>
      <Link href="/new" className="shrink-0 text-[15px] font-bold">
        {t('sampleStart')}
      </Link>
    </div>
  );
}

/** While a case's data is on its way: said in words after a moment (skeletons alone look like a broken page), and
 * again if it takes long. Only for what has nothing to show yet: a page refreshing what it shows needs no words. */
function LoadingNote({ caseId }: { caseId: string }) {
  const fetching = useIsFetching({ queryKey: ['case', caseId], predicate: (q) => q.state.data === undefined }) > 0;
  return <SlowNote active={fetching} className="mb-3" />;
}

/** The rules being researched for some of the case's claims, with a way to watch. */
function ResearchBanner({ items }: { items: CaseHeader['researching'] }) {
  const t = useTranslations('nav');
  const first = items?.[0] as { run_id?: string; title?: string } | undefined;
  if (!first) return null;
  return (
    <div role="status" className="mb-5 flex flex-wrap items-start gap-x-3 gap-y-1 rounded-[12px] border border-rule bg-sheet px-4 py-3">
      <span aria-hidden="true" className="mt-1.5 h-2.5 w-2.5 shrink-0 animate-pulse-soft rounded-full bg-pencil" />
      <p className="m-0 min-w-0 flex-1 text-[15px]">
        <span className="font-bold">{t('researching', { count: items?.length ?? 1, title: first.title ?? '' })}</span>{' '}
        <span className="text-ink-soft">{t('researchingNote')}</span>
      </p>
      {first.run_id ? (
        <Link href={`/challenge/${first.run_id}`} className="shrink-0 text-[15px] font-bold">
          {t('researchWatch')}
        </Link>
      ) : null}
    </div>
  );
}

/** While the case's background job runs: what it is doing, so a screen never looks finished before it is. The
 * screens refresh by themselves when it is done. */
function WorkingBanner({ step }: { step: 'reading' | 'finding' }) {
  const t = useTranslations('nav');
  return (
    <div role="status" className="mb-5 flex items-start gap-3 rounded-[12px] border border-rule bg-sheet px-4 py-3">
      <span aria-hidden="true" className="mt-1.5 h-2.5 w-2.5 shrink-0 animate-pulse-soft rounded-full bg-ledger" />
      <p className="m-0 text-[15px]">
        <span className="font-bold">{step === 'reading' ? t('workingReading') : t('workingFinding')}</span>{' '}
        <span className="text-ink-soft">{t('workingNote')}</span>
      </p>
    </div>
  );
}
