import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { NewCaseScreen } from '@/screens/NewCaseScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/new'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'newCase' });
  return { title: t('title') };
}

export default async function Page({ params }: PageProps<'/[locale]/new'>) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <NewCaseScreen />;
}
