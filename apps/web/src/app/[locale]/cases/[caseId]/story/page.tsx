import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';
import { StoryScreen } from '@/screens/StoryScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/cases/[caseId]/story'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'nav' });
  return { title: t('story') };
}

export default async function StoryPage({ params }: PageProps<'/[locale]/cases/[caseId]/story'>) {
  const { caseId } = await params;
  return <StoryScreen caseId={decodeURIComponent(caseId)} />;
}
