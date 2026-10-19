'use client';
/** A page that does not exist (an old link, a mistyped address, or a page the hosted site leaves out): said plainly,
 * with the way back. */
import { useTranslations } from 'next-intl';
import { ButtonLink, Logo, Wordmark } from '@/components/ui';

export default function NotFound() {
  const t = useTranslations('common');
  return (
    <main id="main" className="grid min-h-screen place-items-center bg-paper px-4 py-10">
      <div className="flex w-full max-w-md flex-col gap-4 rounded-[14px] border border-rule bg-sheet px-6 py-7">
        <span className="flex items-center gap-2.5">
          <Logo />
          <Wordmark />
        </span>
        <h1 className="m-0 font-serif text-[26px] font-semibold">{t('notFound')}</h1>
        <p className="m-0 text-ink-soft">{t('notFoundNote')}</p>
        <div className="flex flex-wrap gap-2.5">
          <ButtonLink href="/">{t('home')}</ButtonLink>
          <ButtonLink href="/new" variant="secondary">
            {t('startCase')}
          </ButtonLink>
        </div>
      </div>
    </main>
  );
}
