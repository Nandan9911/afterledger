'use client';
/**
 * How it is measured: the checks that must always pass (exact, on the six demo families and the shared
 * knowledge), every agent against a simpler baseline, and how well each country is covered. An offline run
 * measures the checks and the baselines; the agents are measured in a live run.
 */
import type { UsageView } from '@afterledger/contracts';
import { Check, X } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { toast } from 'sonner';
import { LanguageSelect } from '@/components/CaseShell';
import { Button, ErrorNote, Logo, ScrollX, Skeleton, Spinner } from '@/components/ui';
import { Link } from '@/i18n/navigation';
import { clockTime, cls, count, intl, longDate, usd } from '@/lib/format';
import { useEvaluation, useMeta, useRunEvaluation, useUsage } from '@/lib/queries';

type CheckRow = { id: string; name: string; ok: boolean; result: string; shows: string; detail: string[] };
type AgentRow = { id: string; module: string; test_set: string; measure: string; baseline: string; baseline_how?: string; agent: string };
type CoverageRow = {
  country: string;
  name: string;
  papers: string;
  leads: string;
  rules: string;
  rules_detail?: string;
  forms: string;
  letters: string;
};
type Report = {
  ran_at: string | null;
  mode: string;
  seconds?: number;
  checks: CheckRow[];
  agents: AgentRow[];
  coverage: CoverageRow[];
  note?: string;
  running?: boolean;
  error?: string;
  live_cases?: string[];
};

const LEVEL_LOOK: Record<string, string> = {
  gold: 'bg-ink text-white',
  verified: 'bg-ledger-tint text-ledger border-[1.5px] border-ledger',
  one_source: 'bg-pencil-tint text-pencil border-[1.5px] border-dashed border-pencil',
  live_only: 'text-blank border-[1.5px] border-dotted border-blank',
  contested: 'bg-debit-tint text-debit border-[1.5px] border-debit',
};

function Level({ level }: { level: string }) {
  const t = useTranslations('evaluation');
  return (
    <span
      className={cls(
        'inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-[13px] font-bold',
        LEVEL_LOOK[level] ?? LEVEL_LOOK.live_only,
      )}
    >
      {t.has(`levels.${level}` as 'levels.gold') ? t(`levels.${level}` as 'levels.gold') : level}
    </span>
  );
}

/** A check's result, ticked or crossed. */
function CheckResult({ c, className }: { c: CheckRow; className?: string }) {
  const t = useTranslations('evaluation');
  return (
    <p className={cls('m-0 font-bold tabular', c.ok ? 'text-ledger' : 'text-debit', className)}>
      <span className="inline-flex items-center gap-1.5">
        {c.ok ? <Check size={16} strokeWidth={3} aria-label={t('pass')} /> : <X size={16} strokeWidth={3} aria-label={t('fail')} />}
        {c.result}
      </span>
    </p>
  );
}

/** What a failing check found, folded away until opened. */
function CheckDetail({ c }: { c: CheckRow }) {
  const t = useTranslations('evaluation');
  if (!c.detail.length) return null;
  return (
    <details className="mt-1.5">
      <summary className="cursor-pointer py-1 font-bold text-debit">{t('details', { count: c.detail.length })}</summary>
      <ul className="m-0 mt-1 ps-5 text-[14px]">
        {c.detail.map((d, i) => (
          <li key={i} className="break-words">
            {d}
          </li>
        ))}
      </ul>
    </details>
  );
}

const th = 'border-b-[1.5px] border-rule-strong px-3 py-2.5 text-start font-semibold text-ink-faint';
const td = 'border-b border-rule px-3 py-3 align-top';
// the first column of a table that scrolls sideways on a phone stays in view
const stick = 'sticky start-0 z-[1] bg-sheet';

const PARTS = ['llm', 'voice', 'store', 'blobs', 'ocr', 'translation', 'grounding', 'search', 'pages', 'screen', 'trace', 'tasks'] as const;

/** What this copy of AfterLedger runs on, part by part: a Google Cloud service or its offline stand-in. */
function RunsOn() {
  const t = useTranslations('evaluation.services');
  const locale = useLocale();
  const meta = useMeta();
  const services: Record<string, string> = meta.data?.services ?? {};
  if (!meta.data) return null;
  const cal = meta.data.calibration;
  const fitted = cal?.status === 'fitted' && cal.fitted_on;
  const failures = Object.entries(meta.data.health?.failures ?? {});
  const slow = Object.entries(meta.data.health?.slow ?? {});
  return (
    <section aria-labelledby="runs-on" className="overflow-x-auto rounded-[14px] border border-rule bg-sheet px-4 py-5 sm:px-5">
      <h2 id="runs-on" className="m-0 font-serif text-[22px] font-semibold">
        {t('title')}
      </h2>
      <p className="m-0 mt-1 text-[15px] text-ink-soft">{t('lede')}</p>
      <table className="mt-3 w-full border-collapse text-[15.5px] sm:min-w-[480px]">
        <tbody>
          {PARTS.filter((k) => services[k]).map((k) => {
            const v = services[k];
            return (
              <tr key={k}>
                <th scope="row" className={cls(td, 'w-[42%] text-start font-bold')}>
                  {t(`part.${k}`)}
                </th>
                <td className={td}>
                  {t.has(`value.${v}` as 'value.local') ? t(`value.${v}` as 'value.local') : v}
                  {k === 'llm' && services.models ? (
                    <span className="block text-[13.5px] text-ink-faint">{t('models', { models: services.models })}</span>
                  ) : null}
                </td>
              </tr>
            );
          })}
          <tr>
            <th scope="row" className={cls(td, 'w-[42%] text-start font-bold')}>
              {t('part.calibration')}
            </th>
            <td className={td}>
              {fitted
                ? t('calibration.fitted', { cases: cal.live_cases?.length ?? 0, date: longDate(cal.fitted_on, locale) })
                : t('calibration.default')}
            </td>
          </tr>
          <tr>
            <th scope="row" className={cls(td, 'w-[42%] text-start font-bold')}>
              {t('part.problems')}
            </th>
            <td className={td}>
              {failures.length ? (
                <ul className="m-0 list-none p-0">
                  {failures.map(([name, f]) => (
                    <li key={name} className="mb-1.5">
                      <span className="font-bold text-debit">{name}</span>:{' '}
                      {t('problem', { count: f.count, time: clockTime(f.at, locale) })}
                      <span className="block break-words text-[13.5px] text-ink-faint">{f.error}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                t('noProblems')
              )}
              {meta.data.health?.strict ? <span className="block text-[13.5px] text-ink-faint">{t('strict')}</span> : null}
            </td>
          </tr>
          {slow.length ? (
            <tr>
              <th scope="row" className={cls(td, 'w-[42%] text-start font-bold')}>
                {t('part.slow')}
              </th>
              <td className={td}>
                <ul className="m-0 list-none p-0">
                  {slow.map(([name, f]) => (
                    <li key={name} className="mb-1.5">
                      <span className="font-bold">{name}</span>: {t('slow', { count: f.count, time: clockTime(f.at, locale) })}
                      {f.passed_over ? <span> · {t('slowNow')}</span> : null}
                      <span className="block break-words text-[13.5px] text-ink-faint">{f.error}</span>
                    </li>
                  ))}
                </ul>
                <span className="block text-[13.5px] text-ink-faint">{t('slowNote')}</span>
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </section>
  );
}

const used = (u: UsageView) => u.tokens_in + u.tokens_out + (u.tokens_thinking ?? 0);

/** Model use: tokens and estimated cost per case (kept with each case's activity), and per agent and model since
 * the service started. Scripted responses use no tokens, so an offline copy says so instead of showing zeros. */
function ModelUse() {
  const t = useTranslations('evaluation.usage');
  const locale = useLocale();
  const q = useUsage();
  const r = q.data;
  if (!r) return null;
  const cases = r.cases.filter((c) => used(c.usage) > 0).slice(0, 20);
  const byCost = (a: [string, UsageView], b: [string, UsageView]) => b[1].cost_usd - a[1].cost_usd;
  const agents = Object.entries(r.by_agent)
    .filter(([, u]) => used(u) > 0)
    .sort(byCost);
  const models = Object.entries(r.by_model)
    .filter(([, u]) => used(u) > 0)
    .sort(byCost);
  const secs = (u: UsageView) =>
    t('seconds', { n: new Intl.NumberFormat(intl(locale), { maximumFractionDigits: 1 }).format(u.calls ? u.ms / u.calls / 1000 : 0) });
  const head = (keys: ReadonlyArray<'case' | 'agent' | 'model' | 'calls' | 'tokens' | 'time' | 'cost'>) => (
    <thead>
      <tr>
        {keys.map((k) => (
          <th key={k} scope="col" className={th}>
            {t(k)}
          </th>
        ))}
      </tr>
    </thead>
  );
  return (
    <section aria-labelledby="use" className="rounded-[14px] border border-rule bg-sheet px-4 py-5 sm:px-5">
      <h2 id="use" className="m-0 font-serif text-[22px] font-semibold">
        {t('title')}
      </h2>
      <p className="m-0 mt-1 text-[15px] text-ink-soft">{t('lede', { date: longDate(r.prices_as_of, locale) })}</p>
      <p className="m-0 mt-1 text-[15px] text-ink-soft">
        {r.budget_usd ? t('budget', { amount: usd(r.budget_usd, locale) }) : t('noBudget')}
      </p>
      {!cases.length && !agents.length ? (
        <p className="m-0 mt-3 text-[15px]">{t('none')}</p>
      ) : (
        <>
          {cases.length ? (
            <>
              <h3 className="m-0 mt-4 text-[16px] font-bold">{t('byCase')}</h3>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full min-w-[560px] border-collapse text-[15px]">
                  {head(['case', 'calls', 'tokens', 'cost'])}
                  <tbody>
                    {cases.map((c) => (
                      <tr key={c.case_id}>
                        <th scope="row" className={cls(td, stick, 'text-start font-bold')}>
                          {c.title}
                        </th>
                        <td className={cls(td, 'tabular')}>{count(c.usage.calls, locale)}</td>
                        <td className={cls(td, 'tabular')}>{count(used(c.usage), locale)}</td>
                        <td className={cls(td, 'tabular')}>{usd(c.usage.cost_usd, locale)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
          {agents.length ? (
            <>
              <h3 className="m-0 mt-5 text-[16px] font-bold">{t('byAgent', { date: longDate(r.since, locale) })}</h3>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full min-w-[640px] border-collapse text-[15px]">
                  {head(['agent', 'calls', 'tokens', 'time', 'cost'])}
                  <tbody>
                    {agents.map(([name, u]) => (
                      <tr key={name}>
                        <th scope="row" className={cls(td, stick, 'text-start font-bold')}>
                          {name.replaceAll('_', ' ')}
                        </th>
                        <td className={cls(td, 'tabular')}>{count(u.calls, locale)}</td>
                        <td className={cls(td, 'tabular')}>{count(used(u), locale)}</td>
                        <td className={cls(td, 'tabular')}>{secs(u)}</td>
                        <td className={cls(td, 'tabular')}>{usd(u.cost_usd, locale)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
          {models.length ? (
            <>
              <h3 className="m-0 mt-5 text-[16px] font-bold">{t('byModel')}</h3>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full min-w-[560px] border-collapse text-[15px]">
                  {head(['model', 'calls', 'tokens', 'time', 'cost'])}
                  <tbody>
                    {models.map(([name, u]) => (
                      <tr key={name}>
                        <th scope="row" className={cls(td, stick, 'text-start font-bold')}>
                          {name}
                        </th>
                        <td className={cls(td, 'tabular')}>{count(u.calls, locale)}</td>
                        <td className={cls(td, 'tabular')}>{count(used(u), locale)}</td>
                        <td className={cls(td, 'tabular')}>{secs(u)}</td>
                        <td className={cls(td, 'tabular')}>{usd(u.cost_usd, locale)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </>
      )}
    </section>
  );
}

export function EvaluationScreen() {
  const t = useTranslations('evaluation');
  const locale = useLocale();
  const q = useEvaluation();
  const run = useRunEvaluation();
  const report = q.data as Report | undefined;
  const live = report?.mode && report.mode !== 'scripted';
  const measured = (report?.live_cases?.length ?? 0) > 0; // agents measured on cases a live model prepared
  const running = report?.running || run.isPending;
  const ranAt = report?.ran_at
    ? new Intl.DateTimeFormat(intl(locale), { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(
        new Date(report.ran_at),
      )
    : null;
  return (
    <div className="min-h-screen bg-paper">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-rule bg-sheet px-4 py-3.5 sm:px-8">
        <Link href="/" className="flex items-center gap-3 text-ink no-underline">
          <Logo size={26} />
          <span className="font-serif text-[20px] font-semibold">AfterLedger</span>
          <span className="border-s border-rule ps-3 text-ink-faint">{t('crumb')}</span>
        </Link>
        <div className="flex flex-wrap items-center gap-2.5">
          <div role="tablist" aria-label={t('runKind')} className="flex gap-1.5">
            <span
              role="tab"
              aria-selected={!live}
              className={cls(
                'rounded-full border-[1.5px] px-3.5 py-1.5 text-[15px]',
                !live ? 'border-ink bg-ink font-bold text-white' : 'border-rule-strong text-ink-soft',
              )}
            >
              {t('offline')}
            </span>
            <span
              role="tab"
              aria-selected={!!live}
              className={cls(
                'rounded-full border-[1.5px] px-3.5 py-1.5 text-[15px]',
                live ? 'border-ink bg-ink font-bold text-white' : 'border-rule-strong text-ink-soft',
              )}
            >
              {t('live')}
            </span>
          </div>
          <LanguageSelect compact />
        </div>
      </header>
      <main id="main" className="mx-auto flex max-w-[1180px] flex-col gap-7 px-4 pb-14 pt-8 sm:px-8">
        <section className="flex flex-wrap items-end justify-between gap-5">
          <div className="max-w-[46em]">
            <h1 className="m-0 font-serif text-[32px] font-semibold leading-tight sm:text-[38px]">{t('title')}</h1>
            <p className="m-0 mt-2 text-[17px] text-ink-soft">{t('lede')}</p>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <Button
              disabled={running}
              onClick={() =>
                run.mutate(undefined, {
                  onSuccess: (out) => toast((out as { note?: string }).note ?? t('started')),
                  onError: (e) => toast.error(e.message),
                })
              }
            >
              {running ? t('running') : t('run')}
            </Button>
            <p className="m-0 text-[14px] text-ink-faint">
              {ranAt ? t('ranAt', { date: ranAt, mode: report?.mode === 'scripted' ? t('offline') : t('live') }) : t('never')}
              {report?.seconds ? ` · ${t('took', { seconds: report.seconds })}` : ''}
            </p>
          </div>
        </section>
        {q.isError ? <ErrorNote error={q.error} retry={() => q.refetch()} /> : null}
        {report?.error ? <ErrorNote error={new Error(report.error)} /> : null}
        {running ? <Spinner label={t('runningLong')} /> : null}
        {!report ? (
          <Skeleton className="h-96" />
        ) : (
          <>
            {report.note ? (
              <p className="m-0 rounded-[12px] border-[1.5px] border-dotted border-blank bg-sheet px-4 py-3 text-[15px]">{report.note}</p>
            ) : null}
            <section aria-labelledby="exact" className="rounded-[14px] border border-rule bg-sheet px-4 py-5 sm:px-5">
              <h2 id="exact" className="m-0 font-serif text-[22px] font-semibold">
                {t('mustPass')}
              </h2>
              {/* a phone lists each check with its result and what it shows; wider screens get the table */}
              <ul className="m-0 mt-3 list-none p-0 md:hidden">
                {report.checks.map((c) => (
                  <li key={c.id} className="border-b border-rule py-3 last:border-b-0">
                    <p className="m-0 font-bold">{c.name}</p>
                    <CheckResult c={c} className="mt-1" />
                    <p className="m-0 mt-1 text-[15px] text-ink-soft">{c.shows}</p>
                    <CheckDetail c={c} />
                  </li>
                ))}
                {!report.checks.length ? <li className="py-3 text-ink-soft">{t('never')}</li> : null}
              </ul>
              <table className="mt-3 hidden w-full border-collapse text-[15.5px] md:table">
                <thead>
                  <tr>
                    <th scope="col" className={th}>
                      {t('check')}
                    </th>
                    <th scope="col" className={th}>
                      {t('result')}
                    </th>
                    <th scope="col" className={th}>
                      {t('shows')}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {report.checks.map((c) => (
                    <tr key={c.id}>
                      <td className={cls(td, 'font-bold')}>{c.name}</td>
                      <td className={cls(td, 'whitespace-nowrap')}>
                        <CheckResult c={c} />
                      </td>
                      <td className={cls(td, 'text-ink-soft')}>
                        {c.shows}
                        <CheckDetail c={c} />
                      </td>
                    </tr>
                  ))}
                  {!report.checks.length ? (
                    <tr>
                      <td colSpan={3} className={cls(td, 'text-ink-soft')}>
                        {t('never')}
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </section>

            <section aria-labelledby="mods" className="rounded-[14px] border border-rule bg-sheet px-4 py-5 sm:px-5">
              <h2 id="mods" className="m-0 font-serif text-[22px] font-semibold">
                {t('agents')}
              </h2>
              <p className="m-0 mt-1 text-[15px] text-ink-soft">{t('agentsLede')}</p>
              {/* below a laptop's width each module is a card: its test, then the baseline and the agents side by side */}
              <ul className="m-0 mt-3 list-none p-0 lg:hidden">
                {report.agents.map((a) => (
                  <li key={a.id} className="border-b border-rule py-3.5 last:border-b-0">
                    <p className="m-0 font-bold">{a.module}</p>
                    <p className="m-0 mt-0.5 text-[15px] text-ink-soft">
                      {a.test_set}. {a.measure}.
                    </p>
                    <dl className="m-0 mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[15px]">
                      <dt className="text-ink-faint">{t('baseline')}</dt>
                      <dd className="m-0 tabular">
                        <span className="font-bold">{a.baseline}</span>
                        {a.baseline_how ? <span className="block text-[13.5px] text-ink-faint">{a.baseline_how}</span> : null}
                      </dd>
                      <dt className="text-ink-faint">{t('agent')}</dt>
                      <dd className={cls('m-0', measured ? 'font-bold' : 'text-ink-faint')}>{a.agent}</dd>
                    </dl>
                  </li>
                ))}
              </ul>
              <table className="mt-3 hidden w-full border-collapse text-[15.5px] lg:table">
                <thead>
                  <tr>
                    {(['module', 'testSet', 'measure', 'baseline', 'agent'] as const).map((k) => (
                      <th key={k} scope="col" className={th}>
                        {t(k)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report.agents.map((a) => (
                    <tr key={a.id}>
                      <td className={cls(td, 'font-bold')}>{a.module}</td>
                      <td className={td}>{a.test_set}</td>
                      <td className={cls(td, 'text-ink-soft')}>{a.measure}</td>
                      <td className={cls(td, 'tabular')}>
                        <span className="font-bold">{a.baseline}</span>
                        {a.baseline_how ? <span className="block text-[13.5px] text-ink-faint">{a.baseline_how}</span> : null}
                      </td>
                      <td className={cls(td, measured ? 'font-bold' : 'text-ink-faint')}>{a.agent}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            <section aria-labelledby="cov" className="rounded-[14px] border border-rule bg-sheet px-4 py-5 sm:px-5">
              <h2 id="cov" className="m-0 font-serif text-[22px] font-semibold">
                {t('coverage')}
              </h2>
              {/* five levels per country do not fit a phone: the table scrolls sideways with the country kept in view */}
              <ScrollX label={t('coverage')} className="mt-3">
                <table className="w-full min-w-[640px] border-collapse text-[15.5px]">
                  <thead>
                    <tr>
                      <th scope="col" className={cls(th, stick)}>
                        {t('country')}
                      </th>
                      {(['papers', 'leads', 'rules', 'forms', 'letters'] as const).map((k) => (
                        <th key={k} scope="col" className={th}>
                          {t(`cols.${k}`)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {report.coverage.map((c) => (
                      <tr key={c.country}>
                        <th scope="row" className={cls(td, stick, 'text-start font-bold')}>
                          {new Intl.DisplayNames([intl(locale)], { type: 'region' }).of(c.country) ?? c.name}
                        </th>
                        {(['papers', 'leads', 'rules', 'forms', 'letters'] as const).map((k) => (
                          <td key={k} className={td}>
                            <Level level={c[k]} />
                            {k === 'rules' && c.rules_detail ? (
                              <span className="mt-1 block text-[13px] text-ink-faint">{c.rules_detail}</span>
                            ) : null}
                          </td>
                        ))}
                      </tr>
                    ))}
                    <tr>
                      <th scope="row" className={cls(td, stick, 'border-b-0 text-start font-bold')}>
                        {t('anyOther')}
                      </th>
                      <td colSpan={5} className={cls(td, 'border-b-0 text-ink-soft')}>
                        {t('researchedLive')}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </ScrollX>
            </section>
          </>
        )}
        <ModelUse />
        <RunsOn />
      </main>
    </div>
  );
}
