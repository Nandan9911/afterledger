'use client';
/** The admin's table of accounts: everyone signed up, with their cases; add, edit or delete an account. */
import type { AccountRow } from '@afterledger/contracts';
import { useLocale, useTranslations } from 'next-intl';
import { useState, type FormEvent } from 'react';
import { toast } from 'sonner';
import { AccountLinks, SignInFirst, useMounted } from '@/components/Account';
import { LanguageSelect } from '@/components/CaseShell';
import { Button, Logo, ScrollX, Skeleton } from '@/components/ui';
import { Link } from '@/i18n/navigation';
import { useAuth } from '@/lib/auth';
import { longDate } from '@/lib/format';
import { useAccounts, useCreateAccount, useDeleteAccount, useEditAccount } from '@/lib/queries';

const field = 'min-h-11 w-full rounded-[10px] border border-rule-strong bg-sheet px-3 text-[16px] font-normal';
const label = 'flex flex-col gap-1 text-[15px] font-bold';

export function AdminScreen() {
  const t = useTranslations('admin');
  const ta = useTranslations('auth');
  const mounted = useMounted();
  const user = useAuth((s) => s.user);
  const isAdmin = mounted && user?.role === 'admin';
  const accounts = useAccounts(isAdmin);

  return (
    <div className="flex min-h-screen flex-col bg-paper">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-rule bg-sheet px-4 py-3.5 sm:px-8">
        <Link href="/" className="flex items-center gap-3 text-ink no-underline">
          <Logo size={26} />
          <span className="font-serif text-[20px] font-semibold">AfterLedger</span>
        </Link>
        <span className="flex flex-wrap items-center gap-x-6 gap-y-2 text-[16px]">
          <AccountLinks />
          <LanguageSelect compact />
        </span>
      </header>
      <main id="main" className="mx-auto flex w-full min-w-0 max-w-[1100px] flex-1 flex-col gap-6 px-4 py-10 sm:px-8">
        <div>
          <h1 className="m-0 font-serif text-[34px] font-semibold leading-tight">{t('title')}</h1>
          <p className="m-0 mt-2 max-w-[44em] text-[17px] text-ink-soft">{t('lede')}</p>
        </div>
        {!mounted ? (
          <Skeleton className="h-40" />
        ) : !user ? (
          <SignInFirst next="/admin" title={ta('signIn')} />
        ) : !isAdmin ? (
          <p role="alert" className="m-0 rounded-[14px] border border-rule bg-sheet p-5 text-[17px]">
            {t('onlyAdmin')}
          </p>
        ) : (
          <>
            <NewAccount />
            {accounts.isLoading ? (
              <Skeleton className="h-40" />
            ) : accounts.error ? (
              <p role="alert" className="m-0 text-debit">
                {accounts.error.message}
              </p>
            ) : (
              <Accounts rows={accounts.data ?? []} />
            )}
          </>
        )}
      </main>
    </div>
  );
}

function NewAccount() {
  const t = useTranslations('admin');
  const create = useCreateAccount();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      { name, email, password },
      {
        onSuccess: () => {
          toast.success(t('createdToast'));
          setName('');
          setEmail('');
          setPassword('');
          setOpen(false);
        },
        onError: (err) => toast.error(err.message),
      },
    );
  }

  if (!open) {
    return (
      <Button variant="secondary" className="self-start" onClick={() => setOpen(true)}>
        {t('add')}
      </Button>
    );
  }
  return (
    <form
      onSubmit={submit}
      aria-label={t('add')}
      className="grid items-end gap-4 rounded-[14px] border border-rule bg-sheet p-5 [grid-template-columns:repeat(auto-fit,minmax(min(220px,100%),1fr))]"
    >
      <label className={label}>
        {t('name')}
        <input value={name} onChange={(e) => setName(e.target.value)} required autoComplete="off" className={field} />
      </label>
      <label className={label}>
        {t('email')}
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="off" className={field} />
      </label>
      <label className={label}>
        {t('password')}
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={8}
          autoComplete="new-password"
          className={field}
        />
      </label>
      <span className="flex flex-wrap gap-2">
        <Button type="submit" disabled={create.isPending}>
          {t('create')}
        </Button>
        <CancelButton onClick={() => setOpen(false)} />
      </span>
    </form>
  );
}

function CancelButton({ onClick }: { onClick: () => void }) {
  const tc = useTranslations('common');
  return (
    <Button variant="secondary" onClick={onClick}>
      {tc('cancel')}
    </Button>
  );
}

function Accounts({ rows }: { rows: AccountRow[] }) {
  const t = useTranslations('admin');
  const locale = useLocale();
  const [editing, setEditing] = useState<string | null>(null);
  const [asking, setAsking] = useState<string | null>(null);
  if (!rows.length) return <p className="m-0 text-ink-soft">{t('empty')}</p>;
  const th = 'border-b border-rule px-3 py-2.5 text-start text-[14px] font-bold text-ink-soft';
  const td = 'border-b border-rule px-3 py-3 align-top text-[16px]';
  return (
    <ScrollX label={t('title')} className="relative w-full min-w-0 rounded-[14px] border border-rule bg-sheet">
      <table className="w-full min-w-[720px] border-collapse">
        <thead>
          <tr>
            <th scope="col" className={th}>
              {t('name')}
            </th>
            <th scope="col" className={th}>
              {t('email')}
            </th>
            <th scope="col" className={th}>
              {t('role')}
            </th>
            <th scope="col" className={th}>
              {t('created')}
            </th>
            <th scope="col" className={th}>
              {t('cases')}
            </th>
            <th scope="col" className={th}>
              <span className="sr-only">{t('actions')}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) =>
            editing === r.id ? (
              <tr key={r.id}>
                <td className={td} colSpan={6}>
                  <EditAccount row={r} done={() => setEditing(null)} />
                </td>
              </tr>
            ) : (
              <tr key={r.id}>
                <td className={`${td} font-bold`}>{r.name}</td>
                <td className={`${td} break-all`}>{r.email}</td>
                <td className={td}>{r.role === 'admin' ? t('roleAdmin') : t('roleUser')}</td>
                <td className={td}>{longDate(r.created_at, locale)}</td>
                <td className={td}>{r.cases}</td>
                <td className={td}>
                  {r.role === 'admin' ? (
                    <span className="text-[14px] text-ink-faint">{t('adminFixed')}</span>
                  ) : asking === r.id ? (
                    <DeleteAccount row={r} done={() => setAsking(null)} />
                  ) : (
                    <span className="flex flex-wrap gap-2">
                      <Button size="sm" variant="secondary" onClick={() => setEditing(r.id)}>
                        {t('edit')}
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => setAsking(r.id)}>
                        {t('delete')}
                      </Button>
                    </span>
                  )}
                </td>
              </tr>
            ),
          )}
        </tbody>
      </table>
    </ScrollX>
  );
}

function EditAccount({ row, done }: { row: AccountRow; done: () => void }) {
  const t = useTranslations('admin');
  const edit = useEditAccount();
  const [name, setName] = useState(row.name);
  const [email, setEmail] = useState(row.email);
  const [password, setPassword] = useState('');

  function submit(e: FormEvent) {
    e.preventDefault();
    edit.mutate(
      { id: row.id, body: { name, email, password: password || null } },
      {
        onSuccess: () => {
          toast.success(t('savedToast'));
          done();
        },
        onError: (err) => toast.error(err.message),
      },
    );
  }

  return (
    <form
      onSubmit={submit}
      aria-label={`${t('edit')}: ${row.name}`}
      className="grid items-end gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(200px,100%),1fr))]"
    >
      <label className={label}>
        {t('name')}
        <input value={name} onChange={(e) => setName(e.target.value)} required className={field} />
      </label>
      <label className={label}>
        {t('email')}
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required className={field} />
      </label>
      <label className={label}>
        {t('newPassword')}
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          className={field}
        />
        <span className="text-[13px] font-normal text-ink-faint">{t('newPasswordHint')}</span>
      </label>
      <span className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={edit.isPending}>
          {t('save')}
        </Button>
        <CancelButton onClick={done} />
      </span>
    </form>
  );
}

function DeleteAccount({ row, done }: { row: AccountRow; done: () => void }) {
  const t = useTranslations('admin');
  const remove = useDeleteAccount();
  return (
    <div role="group" aria-label={t('delete')} className="flex max-w-[22em] flex-col gap-2 rounded-xl bg-paper p-3 text-[15px]">
      <p className="m-0">{t('deleteAsk', { name: row.name })}</p>
      <span className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="danger"
          disabled={remove.isPending}
          onClick={() =>
            remove.mutate(row.id, {
              onSuccess: () => {
                toast.success(t('deletedToast'));
                done();
              },
              onError: (err) => toast.error(err.message),
            })
          }
        >
          {t('deleteYes')}
        </Button>
        <CancelButton onClick={done} />
      </span>
    </div>
  );
}
