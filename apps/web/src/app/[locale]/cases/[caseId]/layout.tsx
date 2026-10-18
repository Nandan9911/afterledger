import { setRequestLocale } from 'next-intl/server';
import { CaseShell } from '@/components/CaseShell';

export default async function CaseLayout({ children, params }: LayoutProps<'/[locale]/cases/[caseId]'>) {
  const { locale, caseId } = await params;
  setRequestLocale(locale);
  return <CaseShell caseId={decodeURIComponent(caseId)}>{children}</CaseShell>;
}
