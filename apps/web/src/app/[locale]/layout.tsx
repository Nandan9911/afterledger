import '@fontsource-variable/atkinson-hyperlegible-next';
import '@fontsource-variable/literata';
import '@fontsource-variable/noto-sans-arabic';
import '@fontsource-variable/noto-sans-devanagari';
import '@fontsource-variable/noto-sans-jp';
import '@fontsource-variable/noto-sans-malayalam';
import '../globals.css';
import type { Metadata, Viewport } from 'next';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { Providers } from '@/components/Providers';
import { routing, rtlLocales } from '@/i18n/routing';

// The API's address is read when the page is served, so one build runs anywhere.
export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: { default: 'AfterLedger', template: '%s · AfterLedger' },
  description: 'Finds what a person left behind and prepares every claim, in any country and language, with the source behind every line.',
  applicationName: 'AfterLedger',
  manifest: '/manifest.webmanifest',
  icons: { icon: '/icon.svg', apple: '/apple-touch-icon.png' },
};

export const viewport: Viewport = {
  themeColor: '#F4F7F3',
  width: 'device-width',
  initialScale: 1,
};

export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

export default async function LocaleLayout({ children, params }: LayoutProps<'/[locale]'>) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);
  const messages = await getMessages();
  const t = await getTranslations({ locale, namespace: 'common' });
  const apiBase = process.env.AL_API_BASE ?? process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8080';
  return (
    <html lang={locale} dir={rtlLocales.includes(locale) ? 'rtl' : 'ltr'}>
      <body>
        <a href="#main" className="sr-only-focusable absolute left-2 top-2 z-50 rounded bg-ink px-3 py-2 text-white">
          {t('skip')}
        </a>
        <NextIntlClientProvider locale={locale} messages={messages}>
          {/* a new cache for each language, since the server writes some words in the page's language */}
          <Providers
            key={locale}
            apiBase={apiBase}
            locale={locale}
            product={process.env.AL_SITE === 'product'}
            auth={process.env.AL_AUTH === 'required' || (process.env.AL_AUTH !== 'off' && process.env.AL_SITE === 'product')}
          >
            {children}
          </Providers>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
