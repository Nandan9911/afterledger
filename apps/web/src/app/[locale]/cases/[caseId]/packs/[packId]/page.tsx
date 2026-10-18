import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';
import { PackScreen } from '@/screens/PackScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/cases/[caseId]/packs/[packId]'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'nav' });
  return { title: t('packs') };
}

export default async function Page({ params }: PageProps<'/[locale]/cases/[caseId]/packs/[packId]'>) {
  const { caseId, packId } = await params;
  return <PackScreen caseId={decodeURIComponent(caseId)} packId={decodeURIComponent(packId)} />;
}
