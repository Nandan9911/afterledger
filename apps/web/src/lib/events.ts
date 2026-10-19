'use client';
/** Live updates for an open case: activity lines and 'something changed' notices over server-sent events. */
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { apiUrl } from './api';

export type ActivityLine = { id: string; text: string; kind: string; module: string; at: string };

/** Whether the page is in front: a tab in the background holds no live stream (the service has a limited number). */
function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => typeof document === 'undefined' || document.visibilityState !== 'hidden');
  useEffect(() => {
    if (typeof document === 'undefined') return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const onChange = () => {
      if (timer) clearTimeout(timer);
      if (document.visibilityState === 'hidden') {
        timer = setTimeout(() => setVisible(false), 30_000); // a quick look at another tab keeps the stream
      } else {
        setVisible(true);
      }
    };
    document.addEventListener('visibilitychange', onChange);
    return () => {
      document.removeEventListener('visibilitychange', onChange);
      if (timer) clearTimeout(timer);
    };
  }, []);
  return visible;
}

export function useCaseEvents(caseId: string | null | undefined): { live: boolean; latest: ActivityLine | null } {
  const qc = useQueryClient();
  const [live, setLive] = useState(false);
  const [latest, setLatest] = useState<ActivityLine | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const visible = usePageVisible();

  useEffect(() => {
    if (!caseId || !visible || typeof EventSource === 'undefined') return;
    const es = new EventSource(apiUrl(`/v1/cases/${encodeURIComponent(caseId)}/events`));
    const refresh = () => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => qc.invalidateQueries({ queryKey: ['case', caseId] }), 350);
    };
    let opened = false;
    es.onopen = () => {
      // after a reconnect (the service closes each stream after a while) anything missed meanwhile is fetched again
      if (opened) refresh();
      opened = true;
      setLive(true);
    };
    // the service ends each stream after a while and the browser reconnects by itself: only a stream given up
    // (refused, or the service gone) shows as not live
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) setLive(false);
    };
    es.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'activity') {
          setLatest(msg.item as ActivityLine);
          refresh();
        } else if (msg.type === 'changed') {
          refresh();
        }
      } catch {
        /* a ping */
      }
    };
    return () => {
      es.close();
      setLive(false);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [caseId, qc, visible]);

  return { live, latest };
}

/** Follows any server-sent event stream, collecting its events (the live challenge's steps, a demo replay). The
 * stream ends on a 'finished' stage, a 'done' or 'error' event, or when the server closes it. */
export function useEventStream<T>(path: string | null, eventName = 'step'): { events: T[]; done: boolean } {
  // State is kept per path, so a new stream starts empty without resetting state inside the effect.
  const [state, setState] = useState<{ path: string | null; events: T[]; done: boolean }>({ path: null, events: [], done: false });
  useEffect(() => {
    if (!path || typeof EventSource === 'undefined') return;
    const es = new EventSource(apiUrl(path));
    let closed = false;
    const update = (fn: (s: { events: T[]; done: boolean }) => { events: T[]; done: boolean }) =>
      setState((prev) => ({ path, ...fn(prev.path === path ? prev : { events: [], done: false }) }));
    const finish = () => {
      if (closed) return;
      closed = true;
      update((s) => ({ ...s, done: true }));
      es.close();
    };
    const onMessage = (e: MessageEvent) => {
      if (!e.data) return;
      try {
        const ev = JSON.parse(e.data) as T & { stage?: string; type?: string; n?: number };
        if (ev.type === 'ping') return;
        // a reconnecting stream replays what was already sent: keep each numbered event once
        update((s) =>
          ev.n !== undefined && s.events.some((p) => (p as { n?: number }).n === ev.n) ? s : { ...s, events: [...s.events, ev] },
        );
        if (ev.stage === 'finished' || ev.type === 'done' || ev.type === 'error') finish();
      } catch {
        /* a ping or a comment */
      }
    };
    for (const name of new Set([eventName, 'done', 'event'])) es.addEventListener(name, onMessage as EventListener);
    es.addEventListener('error', (e) => {
      if ((e as MessageEvent).data) onMessage(e as MessageEvent);
      else if (es.readyState === EventSource.CLOSED) finish();
    });
    return () => {
      closed = true;
      es.close();
    };
  }, [path, eventName]);
  return state.path === path ? { events: state.events, done: state.done } : { events: [], done: false };
}
