'use client';
/**
 * How a screen starts a change and says how it went. A mutation's own callbacks are skipped once the screen that
 * started it has gone, and a screen often goes because of the change itself (the first paper added replaces the
 * empty page; a removed row disappears): until 19 October 2026 an "Uploading…" message then stayed up for good.
 * Here the outcome is always seen. A failure's message comes from the query client (Providers), which also
 * refreshes the page, so a screen only says what to undo.
 */
import { toast } from 'sonner';
import { ApiError } from './api';

type Change<T, V> = { mutateAsync: (vars: V) => Promise<T> };

export function perform<T, V>(
  m: Change<T, V>,
  vars: V,
  opts: {
    working?: string; // said while the change is made, then replaced by the outcome
    done?: (out: T) => string | void; // what to do once it is made; a returned text is said
    failed?: () => void; // what to undo when it was not made
    after?: () => void; // either way
  } = {},
): void {
  const id = opts.working ? toast.loading(opts.working) : undefined;
  m.mutateAsync(vars).then(
    (out) => {
      const said = opts.done?.(out);
      if (said) toast.success(said, { id });
      else if (id !== undefined) toast.dismiss(id);
      opts.after?.();
    },
    () => {
      if (id !== undefined) toast.dismiss(id);
      opts.failed?.();
      opts.after?.();
    },
  );
}

/** What went wrong with a change, in words for the family: the API's own explanation when it refused (a question
 * already answered, an amount it cannot read), or that the change may not have been saved when no answer came. */
export function failureMessage(error: unknown, t: (key: 'mayNotBeSaved' | 'serverTrouble') => string): string {
  if (error instanceof ApiError) return error.status < 500 ? error.message : t('serverTrouble');
  return t('mayNotBeSaved');
}
