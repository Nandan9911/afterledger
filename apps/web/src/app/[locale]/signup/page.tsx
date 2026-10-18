import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { AuthScreen } from '@/screens/AuthScreen';

export async function generateMetadata({ params }: PageProps<'/[locale]/signup'>): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'auth' });
  return { title: t('signUp') };
}

export default async function Page({ params }: PageProps<'/[locale]/signup'>) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <AuthScreen mode="signup" />;
}
