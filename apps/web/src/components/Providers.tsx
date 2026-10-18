'use client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Toaster } from 'sonner';
import { setApiBase, setApiLocale } from '@/lib/api';
import { SiteContext } from '@/lib/site';

export function Providers({
  apiBase,
  locale,
  product = false,
  auth = false,
  children,
}: {
  apiBase: string;
  locale: string;
  product?: boolean;
  auth?: boolean;
  children: React.ReactNode;
}) {
  setApiBase(apiBase);
  setApiLocale(locale);
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false },
          mutations: { retry: 0 },
        },
      }),
  );
  useEffect(() => {
    // the page answers clicks from here on (browser tests wait for this before they click)
    document.documentElement.dataset.hydrated = 'true';
  }, []);
  return (
    <SiteContext.Provider value={{ product, auth }}>
      <QueryClientProvider client={client}>
        {children}
        <Toaster position="bottom-center" toastOptions={{ style: { fontSize: 16 } }} />
      </QueryClientProvider>
    </SiteContext.Provider>
  );
}
