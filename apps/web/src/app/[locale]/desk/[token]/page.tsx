import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { DeskScreen } from '@/screens/DeskScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/desk/[token]'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'desk' });
  return { title: t('title'), robots: { index: false, follow: false } };
}

export default async function Page({ params }: PageProps<'/[locale]/desk/[token]'>) {
  const { locale, token } = await params;
  setRequestLocale(locale);
  return <DeskScreen token={decodeURIComponent(token)} />;
}
