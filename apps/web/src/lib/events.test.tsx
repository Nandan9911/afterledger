// @vitest-environment jsdom
/** Live updates for an open case: a notice refreshes the screen; a reconnect refreshes only if the case changed. */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useCaseEvents } from './events';

class FakeEventSource {
  static CLOSED = 2;
  static last: FakeEventSource | null = null;
  readyState = 1;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  private listeners: Record<string, Array<(e: MessageEvent) => void>> = {};
  constructor(public url: string) {
    FakeEventSource.last = this;
  }
  addEventListener(name: string, fn: (e: MessageEvent) => void) {
    (this.listeners[name] ??= []).push(fn);
  }
  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
  open() {
    this.onopen?.();
  }
  emit(name: string, data: unknown) {
    const e = { data: JSON.stringify(data) } as MessageEvent;
    for (const fn of this.listeners[name] ?? []) fn(e);
  }
}

function Probe({ caseId }: { caseId: string }) {
  useCaseEvents(caseId);
  return null;
}

describe('useCaseEvents', () => {
  let qc: QueryClient;
  let refreshed: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('EventSource', FakeEventSource);
    qc = new QueryClient();
    refreshed = vi.fn();
    qc.invalidateQueries = refreshed as unknown as QueryClient['invalidateQueries'];
    render(
      <QueryClientProvider client={qc}>
        <Probe caseId="case_1" />
      </QueryClientProvider>,
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  const settle = () => act(() => vi.advanceTimersByTime(5000));

  it('refreshes the screen when a named notice arrives', () => {
    const es = FakeEventSource.last!;
    act(() => {
      es.open();
      es.emit('hello', { type: 'hello', seq: 'b1.4' });
    });
    settle();
    expect(refreshed).not.toHaveBeenCalled();
    act(() => es.emit('changed', { type: 'changed', collections: ['claims'], seq: 'b1.5' }));
    settle();
    expect(refreshed).toHaveBeenCalledTimes(1);
  });

  it('refreshes after a reconnect only when the case changed meanwhile', () => {
    const es = FakeEventSource.last!;
    act(() => {
      es.open();
      es.emit('hello', { type: 'hello', seq: 'b1.4' });
    });
    settle();
    // the stream ends and the browser reconnects: the same number, so nothing is fetched again
    act(() => {
      es.open();
      es.emit('hello', { type: 'hello', seq: 'b1.4' });
    });
    settle();
    expect(refreshed).not.toHaveBeenCalled();
    // a change while there was no stream (or a restart, with a new number): fetched again, once
    act(() => {
      es.open();
      es.emit('hello', { type: 'hello', seq: 'b2.0' });
    });
    settle();
    expect(refreshed).toHaveBeenCalledTimes(1);
  });

  it('refreshes after a reconnect to a service that does not say its change number', () => {
    const es = FakeEventSource.last!;
    act(() => {
      es.open();
      es.emit('hello', { type: 'hello', seq: 'b1.4' });
    });
    settle();
    act(() => es.open());
    settle();
    expect(refreshed).toHaveBeenCalledTimes(1);
  });
});
