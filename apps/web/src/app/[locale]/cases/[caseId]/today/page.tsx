import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';
import { TodayScreen } from '@/screens/TodayScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/cases/[caseId]/today'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'nav' });
  return { title: t('today') };
}

export default async function TodayPage({ params }: PageProps<'/[locale]/cases/[caseId]/today'>) {
  const { caseId } = await params;
  return <TodayScreen caseId={decodeURIComponent(caseId)} />;
}
