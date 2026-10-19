/**
 * The cases this browser started or opened. Visitors have no accounts, so on the public site the home page lists
 * only the sample cases and these (the API is told their ids); nobody sees anyone else's case. A case's id is
 * random and works as its private address. Kept in this browser's storage, which can be empty or unavailable
 * (a private window, blocked site data): then the list is simply empty.
 */
const KEY = 'afterledger.cases';
const MAX = 50;
const listeners = new Set<() => void>();

function myCases(): string[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    const list: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list.filter((x): x is string => typeof x === 'string' && /^[\w-]{1,80}$/.test(x)) : [];
  } catch {
    return [];
  }
}

function write(list: string[]) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)));
  } catch {
    /* storage unavailable: the case still works at its address */
  }
  listeners.forEach((fn) => fn());
}

/** The list as one string, so React can tell when it changed (see useSyncExternalStore). */
export function myCasesSnapshot(): string {
  return myCases().join(',');
}

export function rememberCase(id: string) {
  if (!id || id.startsWith('demo-')) return;
  const list = myCases();
  if (list[0] === id) return;
  write([id, ...list.filter((x) => x !== id)]);
}

export function forgetCase(id: string) {
  write(myCases().filter((x) => x !== id));
}

/** Calls back when this browser's list changes (here, or in another tab). */
export function onMyCasesChange(fn: () => void): () => void {
  listeners.add(fn);
  const storage = (e: StorageEvent) => {
    if (e.key === KEY) fn();
  };
  window.addEventListener('storage', storage);
  return () => {
    listeners.delete(fn);
    window.removeEventListener('storage', storage);
  };
}
