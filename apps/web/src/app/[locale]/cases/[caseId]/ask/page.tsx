import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';
import { Suspense } from 'react';
import { AskScreen } from '@/screens/AskScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/cases/[caseId]/ask'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'nav' });
  return { title: t('ask') };
}

export default async function Page({ params }: PageProps<'/[locale]/cases/[caseId]/ask'>) {
  const { caseId } = await params;
  return (
    <Suspense>
      <AskScreen caseId={decodeURIComponent(caseId)} />
    </Suspense>
  );
}
