'use client';
/**
 * The institution's desk: a page a family shares with the bank or insurer handling one claim. It shows the claim
 * against the regulator's checklist, paragraph by paragraph, and what the family has noted; it changes nothing in
 * the case except what the institution may record there: the settlement (which the family confirms before the claim
 * is settled), or a request for something else (which reaches the family as a reply, read against the rules).
 */
import type { DeskView } from '@afterledger/contracts';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useLocale, useTranslations } from 'next-intl';
import { useState, type FormEvent } from 'react';
import { toast } from 'sonner';
import { Button, ErrorNote, Skeleton } from '@/components/ui';
import { Why } from '@/components/Why';
import { api, unwrap } from '@/lib/api';
import { cls, longDate } from '@/lib/format';
import { useDesk } from '@/lib/queries';

function DeskLogo() {
  return (
    <svg width="24" height="24" viewBox="0 0 30 30" aria-hidden="true">
      <rect x="5" y="13" width="20" height="5.5" rx="1" fill="#FFE066" />
      <rect x="4" y="3" width="22" height="24" rx="3" fill="none" stroke="#FFFFFF" strokeWidth="1.8" />
      <path d="M8 9.5h14M8 15.75h14M8 22h9" stroke="#FFFFFF" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function Actions({ token, view }: { token: string; view: DeskView }) {
  const t = useTranslations('desk');
  const tc = useTranslations('common');
  const locale = useLocale();
  const qc = useQueryClient();
  const [mode, setMode] = useState<null | 'settle' | 'ask'>(null);
  const [amount, setAmount] = useState(view.awaiting_family?.amount ?? view.amount?.amount ?? '');
  const [text, setText] = useState('');
  const [rule, setRule] = useState('');
  const done = (data: DeskView, msg: string) => {
    qc.setQueryData(['desk', token], data);
    setMode(null);
    toast.success(msg);
  };
  const settle = useMutation({
    mutationFn: () =>
      unwrap(api().POST('/v1/desk/{token}/settle', { params: { path: { token } }, body: { amount, on: null, note: null } })),
    onSuccess: (d) => done(d, t('recordDone')),
  });
  const ask = useMutation({
    mutationFn: () => unwrap(api().POST('/v1/desk/{token}/request', { params: { path: { token } }, body: { text, rule: rule || null } })),
    onSuccess: (d) => done(d, t('sent')),
  });
  const settled = view.claim.status === 'settled';
  if (settled)
    return <span className="inline-flex min-h-[46px] items-center rounded-[10px] bg-ledger px-4 font-bold text-white">{t('settled')}</span>;
  // a payment recorded here (or in the institution's reply) waits for the family to confirm the money arrived
  const awaiting = view.awaiting_family;
  const field = 'min-h-11 rounded-[10px] border border-rule-strong bg-sheet px-3 text-[17px] font-normal';
  return (
    <div className="flex flex-col items-end gap-3">
      {awaiting ? (
        <p
          role="status"
          className="m-0 max-w-md rounded-[10px] border-[1.5px] border-dashed border-pencil bg-pencil-tint px-4 py-2.5 font-bold text-pencil"
        >
          {t('awaiting', { amount: awaiting.text, date: longDate(view.awaiting_on, locale) })}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2.5">
        <Button variant="secondary" aria-expanded={mode === 'ask'} onClick={() => setMode(mode === 'ask' ? null : 'ask')}>
          {t('askElse')}
        </Button>
        <Button
          variant={awaiting ? 'secondary' : 'primary'}
          aria-expanded={mode === 'settle'}
          onClick={() => setMode(mode === 'settle' ? null : 'settle')}
        >
          {awaiting ? t('change') : t('record')}
        </Button>
      </div>
      {mode === 'settle' ? (
        <form
          className="flex w-full max-w-md flex-col gap-2 rounded-[14px] border border-rule bg-sheet p-4"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (amount.trim()) settle.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-[15px] font-bold">
            {t('recordAmount')}
            <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} className={cls(field, 'tabular')} />
          </label>
          <div className="flex gap-2">
            <Button type="submit" disabled={settle.isPending || !amount.trim()}>
              {awaiting ? t('change') : t('record')}
            </Button>
            <Button variant="secondary" onClick={() => setMode(null)}>
              {tc('cancel')}
            </Button>
          </div>
        </form>
      ) : null}
      {mode === 'ask' ? (
        <form
          className="flex w-full max-w-md flex-col gap-2 rounded-[14px] border border-rule bg-sheet p-4"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (text.trim().length >= 3) ask.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-[15px] font-bold">
            {t('askElseLabel')}
            <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} className={cls(field, 'py-2')} />
          </label>
          <label className="flex flex-col gap-1 text-[15px] font-bold">
            {t('rule')}
            <input value={rule} onChange={(e) => setRule(e.target.value)} placeholder={t('ruleHint')} className={field} />
          </label>
          <div className="flex gap-2">
            <Button type="submit" disabled={ask.isPending || text.trim().length < 3}>
              {tc('send')}
            </Button>
            <Button variant="secondary" onClick={() => setMode(null)}>
              {tc('cancel')}
            </Button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

export function DeskScreen({ token }: { token: string }) {
  const t = useTranslations('desk');
  const locale = useLocale();
  const q = useDesk(token);
  const view = q.data;
  const inst = view?.institution;
  return (
    <div className="min-h-screen bg-paper">
      <header className="flex flex-wrap items-center justify-between gap-4 bg-ink px-4 py-3.5 text-white sm:px-8">
        <div className="flex flex-wrap items-center gap-3">
          <DeskLogo />
          <span className="font-serif text-[19px] font-semibold">{t('title')}</span>
          {inst ? (
            <span className="border-s border-[#45545F] ps-3 text-night-soft">{[inst.name, view?.branch].filter(Boolean).join(', ')}</span>
          ) : null}
        </div>
        {view?.until ? (
          <span className="text-[14px] text-night-soft">
            {t('sharedBy', { family: view.family_label, date: longDate(view.until, locale) })}
          </span>
        ) : null}
      </header>
      <main id="main" className="mx-auto flex max-w-[1280px] flex-col gap-6 px-4 pb-14 pt-7 sm:px-8">
        {q.isError ? (
          <ErrorNote error={new Error(t('invalid'))} />
        ) : !view ? (
          <Skeleton className="h-96" />
        ) : (
          <>
            <section className="flex flex-wrap items-end justify-between gap-5">
              <div>
                <p className="m-0 text-[15px] text-ink-faint">{view.title}</p>
                <h1 className="m-0 mt-1 font-serif text-[30px] font-semibold leading-tight sm:text-[34px]">
                  {t('heading', { name: view.deceased })}
                </h1>
              </div>
              <Actions token={token} view={view} />
            </section>

            <dl className="m-0 grid overflow-hidden rounded-[14px] border border-rule bg-sheet [grid-template-columns:repeat(auto-fit,minmax(min(220px,100%),1fr))]">
              <div className="border-b border-e border-rule px-5 py-4">
                <dt className="text-[14px] text-ink-faint">{t('claimant')}</dt>
                <dd className="m-0 mt-1 font-bold">
                  {view.claimant}
                  {view.claimant_relationship ? `, ${view.claimant_relationship}` : ''}
                </dd>
              </div>
              <div className="border-b border-e border-rule px-5 py-4">
                <dt className="text-[14px] text-ink-faint">{t('amount')}</dt>
                <dd className="m-0 mt-1 font-bold tabular">{view.amount?.text ?? '—'}</dd>
              </div>
              <div className="border-b border-e border-rule px-5 py-4">
                <dt className="text-[14px] text-ink-faint">{t('route')}</dt>
                <dd className="m-0 mt-1 font-bold">{view.route}</dd>
              </div>
              <div className={cls('border-b border-rule px-5 py-4', /overdue|late/i.test(view.due) && 'bg-debit-tint')}>
                <dt className={cls('text-[14px]', /overdue|late/i.test(view.due) ? 'text-debit' : 'text-ink-faint')}>{t('due')}</dt>
                <dd className={cls('m-0 mt-1 font-bold', /overdue|late/i.test(view.due) && 'text-debit')}>{view.due || '—'}</dd>
              </div>
            </dl>

            <div className="flex flex-wrap items-start gap-6">
              <section
                aria-labelledby="check"
                className="min-w-0 flex-[3_1_600px] overflow-x-auto rounded-[14px] border border-rule bg-sheet px-[22px] py-5"
              >
                <h2 id="check" className="m-0 font-serif text-[22px] font-semibold">
                  {t('checkedTitle')}
                </h2>
                {view.checked_against ? <p className="m-0 mt-1 text-[15px] text-ink-soft">{view.checked_against}</p> : null}
                <table className="mt-3.5 w-full min-w-[620px] border-collapse text-[15px]">
                  <thead>
                    <tr className="text-ink-faint">
                      <th scope="col" className="border-b-[1.5px] border-rule-strong py-2 pe-2 text-start font-semibold">
                        {t('requirement')}
                      </th>
                      <th scope="col" className="border-b-[1.5px] border-rule-strong p-2 text-start font-semibold">
                        {t('para')}
                      </th>
                      <th scope="col" className="border-b-[1.5px] border-rule-strong p-2 text-start font-semibold">
                        {t('inPack')}
                      </th>
                      <th scope="col" className="border-b-[1.5px] border-rule-strong py-2 ps-2 text-start font-semibold">
                        {t('status')}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.requirements.map((r, i) => (
                      <tr key={i}>
                        <td className="border-b border-rule py-2.5 pe-2">{r.requirement}</td>
                        <td className="whitespace-nowrap border-b border-rule p-2.5">
                          {r.cites.length ? (
                            <Why ids={r.cites} cites={view.cites} title={r.requirement}>
                              {r.para || undefined}
                            </Why>
                          ) : (
                            r.para || '—'
                          )}
                        </td>
                        <td className="border-b border-rule p-2.5">{r.in_pack}</td>
                        <td
                          className={cls(
                            'border-b border-rule py-2.5 ps-2 font-bold tabular',
                            r.late ? 'text-debit' : /received|paid|met/i.test(r.status) ? 'text-ledger' : '',
                          )}
                        >
                          {r.status}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
              <aside className="flex min-w-0 flex-[2_1_320px] flex-col gap-4">
                {view.notes.length ? (
                  <section aria-labelledby="notes" className="rounded-[14px] border border-rule bg-sheet px-[22px] py-5">
                    <h2 id="notes" className="m-0 font-serif text-[20px] font-semibold">
                      {t('notes')}
                    </h2>
                    {view.notes.map((n, i) => (
                      <p key={i} className="m-0 mt-2">
                        {n}
                      </p>
                    ))}
                    <p className="m-0 mt-2.5 text-[15px] text-ink-soft">{t('seeValues')}</p>
                  </section>
                ) : null}
                <section className="rounded-[14px] border border-rule bg-ledger-tint px-[22px] py-5">
                  <h2 className="m-0 text-[17px] font-bold">{t('whyTitle')}</h2>
                  <p className="m-0 mt-2">{t('whyText')}</p>
                </section>
              </aside>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
