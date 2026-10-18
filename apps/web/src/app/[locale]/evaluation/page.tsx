import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { EvaluationScreen } from '@/screens/EvaluationScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/evaluation'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'evaluation' });
  return { title: t('title') };
}

// the hosted product has no evaluation pages; the working copy keeps them (decided when the page is served)
export const dynamic = 'force-dynamic';

export default async function Page({ params }: PageProps<'/[locale]/evaluation'>) {
  if (process.env.AL_SITE === 'product') notFound();
  const { locale } = await params;
  setRequestLocale(locale);
  return <EvaluationScreen />;
}
