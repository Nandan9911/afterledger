import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';
import { Suspense } from 'react';
import { PapersScreen } from '@/screens/PapersScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/cases/[caseId]/papers'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'nav' });
  return { title: t('papers') };
}

export default async function Page({ params }: PageProps<'/[locale]/cases/[caseId]/papers'>) {
  const { caseId } = await params;
  return (
    <Suspense>
      <PapersScreen caseId={decodeURIComponent(caseId)} />
    </Suspense>
  );
}
