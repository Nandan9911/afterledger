import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';
import { PlanScreen } from '@/screens/PlanScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/cases/[caseId]/plans/[planId]'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'nav' });
  return { title: t('plans') };
}

export default async function Page({ params }: PageProps<'/[locale]/cases/[caseId]/plans/[planId]'>) {
  const { caseId, planId } = await params;
  return <PlanScreen caseId={decodeURIComponent(caseId)} planId={decodeURIComponent(planId)} />;
}
