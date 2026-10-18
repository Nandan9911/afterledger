import { setRequestLocale } from 'next-intl/server';
import { MainScreen } from '@/screens/MainScreen';

export default async function Home({ params }: PageProps<'/[locale]'>) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <MainScreen />;
}
