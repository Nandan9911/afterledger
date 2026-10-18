'use client';
/**
 * Watching a demo case from its first paper: the API replays the family's timeline through the same
 * services a real case uses, a step every couple of seconds, and every open screen follows along live.
 */
import { useQueryClient } from '@tanstack/react-query';
import { useLocale, useTranslations } from 'next-intl';
import { useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { usePathname, useRouter } from '@/i18n/navigation';
import { useEventStream } from '@/lib/events';
import { dayMonth } from '@/lib/format';

type WatchEvent = {
  type?: string;
  on?: string;
  do?: string;
  detail?: Record<string, unknown>;
  error?: string;
  warnings?: string[];
};

export function WatchBanner({ demoKey, delay = 2 }: { demoKey: string; delay?: number }) {
  const t = useTranslations('watch');
  const locale = useLocale();
  const qc = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const { events, done } = useEventStream<WatchEvent>(`/v1/demo/${encodeURIComponent(demoKey)}/watch?delay=${delay}`, 'event');
  const steps = events.filter((e) => e.type === 'event' || e.do);
  const last = steps[steps.length - 1];
  const finished = events.find((e) => e.type === 'done');
  const failed = events.find((e) => e.type === 'error');
  const announced = useRef(false);

  useEffect(() => {
    if (steps.length) qc.invalidateQueries();
  }, [steps.length, qc]);

  useEffect(() => {
    if ((finished || failed || done) && !announced.current) {
      announced.current = true;
      qc.invalidateQueries();
      const skipped = finished?.warnings?.length ?? 0;
      if (failed) toast.error(t('failed', { error: failed.error ?? '' }));
      else if (skipped) toast.warning(t('doneWarnings', { count: skipped }));
      else toast.success(t('done'));
      router.replace(pathname);
    }
  }, [finished, failed, done, qc, router, pathname, t]);

  const what = last?.do
    ? t.has(`do.${last.do}` as 'do.intake')
      ? t(`do.${last.do}` as 'do.intake')
      : last.do.replaceAll('_', ' ')
    : t('starting');
  return (
    <div
      role="status"
      aria-live="polite"
      className="sticky top-0 z-20 mb-5 flex flex-wrap items-center gap-3 rounded-[14px] border-[1.5px] border-ink bg-hl/90 px-4 py-2.5 text-ink shadow-[0_10px_24px_-18px_rgba(20,32,43,0.6)]"
    >
      <span className="h-2.5 w-2.5 animate-pulse-soft rounded-full bg-ink" aria-hidden="true" />
      <span className="font-bold">{t('title')}</span>
      <span>
        {last?.on ? `${dayMonth(last.on, locale)}: ` : ''}
        {what}
      </span>
      <span className="ms-auto text-[14px]">{t('step', { n: steps.length })}</span>
    </div>
  );
}
