import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';
import { Suspense } from 'react';
import { PacksScreen } from '@/screens/PacksScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/cases/[caseId]/packs'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'nav' });
  return { title: t('packs') };
}

export default async function Page({ params }: PageProps<'/[locale]/cases/[caseId]/packs'>) {
  const { caseId } = await params;
  return (
    <Suspense>
      <PacksScreen caseId={decodeURIComponent(caseId)} />
    </Suspense>
  );
}
