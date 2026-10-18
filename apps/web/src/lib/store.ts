'use client';
/** Who is looking at each case, remembered per browser (a convenience only; the case itself is on the server). */
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

type ViewerState = {
  viewers: Record<string, string>;
  setViewer: (caseId: string, personId: string) => void;
};

const safeStorage = createJSONStorage(() => {
  try {
    const probe = '__al_probe__';
    window.localStorage.setItem(probe, '1');
    window.localStorage.removeItem(probe);
    return window.localStorage;
  } catch {
    const mem = new Map<string, string>();
    return {
      getItem: (k: string) => mem.get(k) ?? null,
      setItem: (k: string, v: string) => void mem.set(k, v),
      removeItem: (k: string) => void mem.delete(k),
    };
  }
});

export const useViewerStore = create<ViewerState>()(
  persist(
    (set) => ({
      viewers: {},
      setViewer: (caseId, personId) => set((s) => ({ viewers: { ...s.viewers, [caseId]: personId } })),
    }),
    { name: 'afterledger-viewers', storage: safeStorage },
  ),
);

export function useViewer(caseId: string): string | null {
  return useViewerStore((s) => s.viewers[caseId] ?? null);
}
