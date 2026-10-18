import { redirect } from '@/i18n/navigation';

export default async function CaseIndex({ params }: PageProps<'/[locale]/cases/[caseId]'>) {
  const { locale, caseId } = await params;
  redirect({ href: `/cases/${caseId}/today`, locale });
}
