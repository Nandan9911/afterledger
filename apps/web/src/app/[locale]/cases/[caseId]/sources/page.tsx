import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';
import { Suspense } from 'react';
import { SourcesScreen } from '@/screens/SourcesScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/cases/[caseId]/sources'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'nav' });
  return { title: t('sources') };
}

export default async function Page({ params }: PageProps<'/[locale]/cases/[caseId]/sources'>) {
  const { caseId } = await params;
  return (
    <Suspense>
      <SourcesScreen caseId={decodeURIComponent(caseId)} />
    </Suspense>
  );
}
