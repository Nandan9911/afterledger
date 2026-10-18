import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';
import { Suspense } from 'react';
import { LeadsScreen } from '@/screens/LeadsScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/cases/[caseId]/leads'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'nav' });
  return { title: t('leads') };
}

export default async function Page({ params }: PageProps<'/[locale]/cases/[caseId]/leads'>) {
  const { caseId } = await params;
  return (
    <Suspense>
      <LeadsScreen caseId={decodeURIComponent(caseId)} />
    </Suspense>
  );
}
