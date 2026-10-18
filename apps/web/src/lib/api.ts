/**
 * The typed API client. Paths and bodies come from the API's OpenAPI document (packages/contracts), so a
 * changed endpoint is a type error here, not a broken screen.
 */
import type { paths } from '@afterledger/contracts';
import createClient from 'openapi-fetch';
import { authToken, useAuth } from './auth';

let base = (process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8080').replace(/\/$/, '');
let uiLocale = 'en';

/** Every request says which language the page is in, so the words the server writes come back in it, and carries
 * the sign-in of whoever is signed in. A sign-in the server no longer accepts (expired, or the password changed)
 * is dropped, so the page asks to sign in again. */
function make(baseUrl: string) {
  const c = createClient<paths>({ baseUrl });
  c.use({
    onRequest({ request }) {
      request.headers.set('Accept-Language', uiLocale);
      const token = authToken();
      if (token) request.headers.set('Authorization', `Bearer ${token}`);
      return request;
    },
    onResponse({ request, response }) {
      if (response.status === 401 && request.headers.has('Authorization')) useAuth.getState().signOut();
      return response;
    },
  });
  return c;
}

let client = make(base);

const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]']);

/** A phone on the same Wi-Fi opens the laptop's dev server by the laptop's address (http://192.168.1.23:3000).
 * An API set as localhost is then the laptop's own API at that same address, not the phone's localhost. */
export function apiBaseFor(configured: string, pageHost?: string): string {
  const clean = configured.replace(/\/$/, '');
  if (!pageHost || LOOPBACK.has(pageHost)) return clean;
  try {
    const u = new URL(clean);
    if (!LOOPBACK.has(u.hostname)) return clean;
    u.hostname = pageHost;
    return u.toString().replace(/\/$/, '');
  } catch {
    return clean;
  }
}

export function setApiBase(next: string): void {
  const clean = apiBaseFor(next, typeof window === 'undefined' ? undefined : window.location.hostname);
  if (clean && clean !== base) {
    base = clean;
    client = make(base);
  }
}

export function setApiLocale(next: string): void {
  uiLocale = next;
}

export function api() {
  return client;
}

/** An absolute URL for something the API serves (page images, PDFs, event streams). A browser cannot add headers
 * to these, so the sign-in rides along as access_token. */
export function apiUrl(path: string): string {
  const url = /^https?:\/\//.test(path) ? path : `${base}${path.startsWith('/') ? '' : '/'}${path}`;
  const token = authToken();
  if (!token || !url.startsWith(base)) return url;
  return `${url}${url.includes('?') ? '&' : '?'}access_token=${encodeURIComponent(token)}`;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Unwraps an openapi-fetch result: the data, or an ApiError with the API's own explanation. */
export async function unwrap<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (error !== undefined || !response.ok) {
    const detail =
      error && typeof error === 'object' && 'detail' in error
        ? typeof (error as { detail: unknown }).detail === 'string'
          ? (error as { detail: string }).detail
          : JSON.stringify((error as { detail: unknown }).detail)
        : response.statusText;
    throw new ApiError(response.status, detail || `Request failed (${response.status})`);
  }
  return data as T;
}

/** Uploads papers (multipart), which openapi-fetch does not type well. */
export async function uploadPapers(
  caseId: string,
  files: File[],
  opts: { by?: string | null; title?: string | null; together?: boolean } = {},
) {
  const form = new FormData();
  for (const f of files) form.append('files', f, f.name);
  if (opts.by) form.append('by', opts.by);
  if (opts.title) form.append('title', opts.title);
  form.append('together', opts.together ? 'true' : 'false');
  const res = await fetch(apiUrl(`/v1/cases/${encodeURIComponent(caseId)}/papers`), { method: 'POST', body: form });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}
