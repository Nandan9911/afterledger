'use client';
/**
 * Who is signed in on this browser: the account and its sign-in token, kept in this browser's storage (a
 * private window or blocked site data keeps it in memory instead, until the tab closes). Every API call carries
 * the token (lib/api); addresses the browser opens itself (event streams, images, downloads, the voice socket)
 * carry it as access_token.
 */
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

type Account = { id: string; name: string; email: string; role: 'user' | 'admin' };

type AuthState = {
  token: string | null;
  user: Account | null;
  signIn: (token: string, user: Account) => void;
  signOut: () => void;
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

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      signIn: (token, user) => set({ token, user }),
      signOut: () => set({ token: null, user: null }),
    }),
    { name: 'afterledger-auth', storage: safeStorage },
  ),
);

/** The token for code outside React (the API client). */
export function authToken(): string | null {
  try {
    return useAuth.getState().token;
  } catch {
    return null;
  }
}

/** A path inside the app to come back to after signing in: only a same-site path, never another site. */
export function safeNext(next: string | null | undefined, fallback = '/'): string {
  return next && next.startsWith('/') && !next.startsWith('//') ? next : fallback;
}
