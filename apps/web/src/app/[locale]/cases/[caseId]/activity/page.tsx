import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';
import { Suspense } from 'react';
import { ActivityScreen } from '@/screens/ActivityScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/cases/[caseId]/activity'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'nav' });
  return { title: t('activity') };
}

export default async function Page({ params }: PageProps<'/[locale]/cases/[caseId]/activity'>) {
  const { caseId } = await params;
  return (
    <Suspense>
      <ActivityScreen caseId={decodeURIComponent(caseId)} />
    </Suspense>
  );
}
