import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';
import { Suspense } from 'react';
import { MoneyScreen } from '@/screens/MoneyScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/cases/[caseId]/money'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'nav' });
  return { title: t('money') };
}

export default async function Page({ params }: PageProps<'/[locale]/cases/[caseId]/money'>) {
  const { caseId } = await params;
  return (
    <Suspense>
      <MoneyScreen caseId={decodeURIComponent(caseId)} />
    </Suspense>
  );
}
