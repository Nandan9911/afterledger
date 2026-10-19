import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const said: { kind: string; text?: string; id?: unknown }[] = [];
vi.mock('sonner', () => ({
  toast: {
    loading: (text: string) => (said.push({ kind: 'loading', text }), 'toast-1'),
    success: (text: string, o?: { id?: unknown }) => said.push({ kind: 'success', text, id: o?.id }),
    dismiss: (id: unknown) => said.push({ kind: 'dismiss', id }),
  },
}));

const { failureMessage, perform } = await import('./changes');
const { ApiError, api, patientFetch, setApiBase } = await import('./api');

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('a change a screen starts', () => {
  beforeEach(() => {
    said.length = 0;
  });

  it('says how it went even when the screen that started it has gone', async () => {
    const steps: string[] = [];
    perform({ mutateAsync: async (n: number) => n * 2 }, 21, {
      working: 'Uploading…',
      done: (out) => (steps.push(`done ${out}`), 'Added 1 paper'),
      after: () => steps.push('after'),
    });
    await settle();
    expect(steps).toEqual(['done 42', 'after']);
    expect(said).toEqual([
      { kind: 'loading', text: 'Uploading…' },
      { kind: 'success', text: 'Added 1 paper', id: 'toast-1' },
    ]);
  });

  it('only ends its waiting message when it failed: the query client says what went wrong', async () => {
    const steps: string[] = [];
    perform({ mutateAsync: async () => Promise.reject(new Error('no')) }, null, {
      working: 'Working…',
      done: () => 'never',
      failed: () => steps.push('undo'),
      after: () => steps.push('after'),
    });
    await settle();
    expect(steps).toEqual(['undo', 'after']);
    expect(said).toEqual([
      { kind: 'loading', text: 'Working…' },
      { kind: 'dismiss', id: 'toast-1' },
    ]);
  });

  it('is explained in words for the family', () => {
    const t = (k: string) => `<${k}>`;
    expect(failureMessage(new ApiError(409, 'This question has already been answered.'), t)).toBe(
      'This question has already been answered.',
    );
    expect(failureMessage(new ApiError(502, 'Bad Gateway'), t)).toBe('<serverTrouble>');
    expect(failureMessage(new TypeError('Failed to fetch'), t)).toBe('<mayNotBeSaved>');
  });
});

describe('a request whose answer was lost', () => {
  const real = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = real;
    vi.useRealTimers();
  });

  it('is sent again, with the same key, until an answer comes', async () => {
    vi.useFakeTimers();
    const keys: (string | null)[] = [];
    const answers = [() => Promise.reject(new TypeError('Failed to fetch')), () => new Response('', { status: 502 })];
    globalThis.fetch = vi.fn(async (r: RequestInfo | URL) => {
      keys.push((r as Request).headers.get('Idempotency-Key'));
      const next = answers.shift();
      return next ? next() : new Response('{"ok":true}', { status: 200 });
    }) as typeof fetch;
    const request = new Request('https://api.example.org/v1/x', { method: 'POST', headers: { 'Idempotency-Key': 'k-1' } });
    const done = patientFetch(request);
    await vi.runAllTimersAsync();
    expect((await done).status).toBe(200);
    expect(keys).toEqual(['k-1', 'k-1', 'k-1']);
  });

  it('is given up after three tries', async () => {
    vi.useFakeTimers();
    globalThis.fetch = vi.fn(async () => new Response('', { status: 503 })) as typeof fetch;
    const done = patientFetch(new Request('https://api.example.org/v1/x'));
    await vi.runAllTimersAsync();
    expect((await done).status).toBe(503);
    expect(globalThis.fetch).toHaveBeenCalledTimes(3);
  });

  it('a change carries a key of its own; a read carries none', async () => {
    const seen: [string, string | null][] = [];
    globalThis.fetch = vi.fn(async (r: RequestInfo | URL) => {
      const req = r as Request;
      seen.push([req.method, req.headers.get('Idempotency-Key')]);
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;
    setApiBase('https://api.example.org');
    await api().GET('/v1/meta');
    await api().POST('/v1/cases', { body: { lang: 'en' } });
    await api().POST('/v1/cases', { body: { lang: 'en' } });
    expect(seen[0]).toEqual(['GET', null]);
    expect(seen[1][1]).toBeTruthy();
    expect(seen[2][1]).not.toBe(seen[1][1]);
  });
});
