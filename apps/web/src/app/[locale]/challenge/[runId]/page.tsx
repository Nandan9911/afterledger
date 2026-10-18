import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { ChallengeRunScreen } from '@/screens/ChallengeScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/challenge/[runId]'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'challenge' });
  return { title: t('title') };
}

export default async function Page({ params }: PageProps<'/[locale]/challenge/[runId]'>) {
  const { locale, runId } = await params;
  setRequestLocale(locale);
  return <ChallengeRunScreen runId={decodeURIComponent(runId)} />;
}
