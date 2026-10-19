'use client';
import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useTranslations } from 'next-intl';
import { useEffect, useState } from 'react';
import { toast, Toaster } from 'sonner';
import { setApiBase, setApiLocale } from '@/lib/api';
import { failureMessage } from '@/lib/changes';
import { SiteContext } from '@/lib/site';

declare module '@tanstack/react-query' {
  interface Register {
    // quiet: the screen shows what went wrong itself (the sign-in form), so no message pops up as well
    mutationMeta: { quiet?: boolean };
  }
}

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
  const t = useTranslations('common');
  // made once per language (the layout gives each language its own Providers), so t stays this page's
  const [client] = useState(() => {
    const qc: QueryClient = new QueryClient({
      defaultOptions: {
        queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false },
        mutations: { retry: 0 },
      },
      // every change that fails says so, and the page then shows what the API has: a change can go through when
      // its answer is lost on the way (the request is first sent again, lib/api.ts), and one refused is out of date
      mutationCache: new MutationCache({
        onError: (error, _vars, _ctx, mutation) => {
          void qc.invalidateQueries();
          if (!mutation.meta?.quiet) toast.error(failureMessage(error, t));
        },
      }),
    });
    return qc;
  });
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
