'use client';
/**
 * Signing in, in the page header: "Sign in" and "Create account" for a visitor; the account's name, "Accounts" for
 * the admin, and "Sign out" once signed in. Shown only where sign-in is required. The sign-in lives in this
 * browser's storage, so what depends on it is drawn after the page has loaded (the server cannot know it).
 */
import { useTranslations } from 'next-intl';
import { useSyncExternalStore } from 'react';
import { Link, usePathname } from '@/i18n/navigation';
import { useAuth } from '@/lib/auth';
import { useSignOut } from '@/lib/queries';
import { useSite } from '@/lib/site';
import { buttonClass } from './ui';

const noop = () => () => {};

/** True once the page runs in the browser (false while the server draws it). */
export function useMounted(): boolean {
  return useSyncExternalStore(
    noop,
    () => true,
    () => false,
  );
}

const link = 'text-ink no-underline hover:underline pointer-coarse:inline-flex pointer-coarse:min-h-11 pointer-coarse:items-center';

export function AccountLinks() {
  const t = useTranslations('auth');
  const site = useSite();
  const mounted = useMounted();
  const user = useAuth((s) => s.user);
  const signOut = useSignOut();
  const pathname = usePathname();
  if (!site.auth || !mounted) return null;
  const next = `?next=${encodeURIComponent(pathname)}`;
  if (!user) {
    return (
      <span className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <Link href={`/signin${next}`} className={link}>
          {t('signIn')}
        </Link>
        <Link href={`/signup${next}`} className={buttonClass('secondary', 'sm')}>
          {t('signUp')}
        </Link>
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-x-5 gap-y-2">
      <span className="text-[15px] text-ink-soft">{t('signedInAs', { name: user.name })}</span>
      {user.role === 'admin' ? (
        <Link href="/admin" className={link}>
          {t('accounts')}
        </Link>
      ) : null}
      <button type="button" onClick={signOut} className={`${link} cursor-pointer border-0 bg-transparent p-0 text-[16px]`}>
        {t('signOut')}
      </button>
    </span>
  );
}

/** Where a visitor must sign in before going on: why, and the two ways in, coming back here afterwards. */
export function SignInFirst({ next, title }: { next: string; title?: string }) {
  const t = useTranslations('auth');
  const q = `?next=${encodeURIComponent(next)}`;
  return (
    <section aria-labelledby="sign-in-first" className="flex flex-col gap-4 rounded-[16px] border border-rule bg-sheet p-6">
      <h2 id="sign-in-first" className="m-0 font-serif text-[24px] font-semibold leading-snug">
        {title ?? t('needSignIn')}
      </h2>
      <p className="m-0 text-[17px] text-ink-soft">{t('needSignInText')}</p>
      <div className="flex flex-wrap gap-3">
        <Link href={`/signin${q}`} className={buttonClass('primary', 'md')}>
          {t('signIn')}
        </Link>
        <Link href={`/signup${q}`} className={buttonClass('secondary', 'md')}>
          {t('signUp')}
        </Link>
      </div>
    </section>
  );
}
