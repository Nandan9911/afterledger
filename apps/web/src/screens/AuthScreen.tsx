'use client';
/** Signing in, or creating an account: one short form each, then back to where the visitor was going. */
import { useTranslations } from 'next-intl';
import { useSearchParams } from 'next/navigation';
import { useState, type FormEvent } from 'react';
import { LanguageSelect } from '@/components/CaseShell';
import { Button, Logo } from '@/components/ui';
import { Link, useRouter } from '@/i18n/navigation';
import { safeNext } from '@/lib/auth';
import { useSignIn, useSignUp } from '@/lib/queries';

const field = 'min-h-12 rounded-[10px] border border-rule-strong bg-sheet px-3 text-[17px] font-normal';
const label = 'flex flex-col gap-1.5 text-[16px] font-bold';

export function AuthScreen({ mode }: { mode: 'signin' | 'signup' }) {
  const t = useTranslations('auth');
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get('next'));
  const signIn = useSignIn();
  const signUp = useSignUp();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const busy = signIn.isPending || signUp.isPending;
  const error = (mode === 'signin' ? signIn.error : signUp.error)?.message;

  function submit(e: FormEvent) {
    e.preventDefault();
    const done = { onSuccess: () => router.push(next) };
    if (mode === 'signin') signIn.mutate({ email, password }, done);
    else signUp.mutate({ name, email, password }, done);
  }

  const other = `?next=${encodeURIComponent(next)}`;
  return (
    <div className="flex min-h-screen flex-col bg-paper">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-rule bg-sheet px-4 py-3.5 sm:px-8">
        <Link href="/" className="flex items-center gap-3 text-ink no-underline">
          <Logo size={26} />
          <span className="font-serif text-[20px] font-semibold">AfterLedger</span>
        </Link>
        <LanguageSelect compact />
      </header>
      <main id="main" className="mx-auto flex w-full max-w-[520px] flex-1 flex-col justify-center gap-6 px-4 py-10">
        <div>
          <h1 className="m-0 font-serif text-[34px] font-semibold leading-tight">
            {mode === 'signin' ? t('signInTitle') : t('signUpTitle')}
          </h1>
          <p className="m-0 mt-2 text-[18px] text-ink-soft">{mode === 'signin' ? t('signInLede') : t('signUpLede')}</p>
        </div>
        <form onSubmit={submit} className="flex flex-col gap-4 rounded-[16px] border border-rule bg-sheet p-6" noValidate>
          {mode === 'signup' ? (
            <label className={label}>
              {t('name')}
              <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required className={field} />
            </label>
          ) : null}
          <label className={label}>
            {t('email')}
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              inputMode="email"
              required
              className={field}
            />
          </label>
          <label className={label}>
            {t('password')}
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
              required
              minLength={mode === 'signup' ? 8 : undefined}
              aria-describedby={mode === 'signup' ? 'password-hint' : undefined}
              className={field}
            />
          </label>
          {/* the hint describes the field, outside its name */}
          {mode === 'signup' ? (
            <span id="password-hint" className="-mt-2.5 text-[14px] text-ink-faint">
              {t('passwordHint')}
            </span>
          ) : null}
          {error ? (
            <p role="alert" className="m-0 rounded-[10px] border border-debit bg-debit-tint px-3 py-2 text-[16px] font-bold text-debit">
              {error}
            </p>
          ) : null}
          <Button type="submit" size="lg" disabled={busy} className="self-start">
            {mode === 'signin' ? t('submitSignIn') : t('submitSignUp')}
          </Button>
        </form>
        <p className="m-0 text-[16px] text-ink-soft">
          {mode === 'signin' ? t('noAccount') : t('haveAccount')}{' '}
          <Link href={`${mode === 'signin' ? '/signup' : '/signin'}${other}`} className="font-bold">
            {mode === 'signin' ? t('signUp') : t('signIn')}
          </Link>
        </p>
      </main>
    </div>
  );
}
