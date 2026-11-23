/** Where the app gets its data: the in-browser demo, or a running API. */
export type Connection = { mode: 'demo' } | { mode: 'api'; url: string };

export const CONNECTION_KEY = 'doc-intake-connection:v1';

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/**
 * The connection to start with. A `?api=` query parameter wins (handy for
 * sharing a link to a local API), then the last saved choice, then demo mode.
 */
export function initialConnection(search: string, storage: StorageLike | null): Connection {
  const fromQuery = new URLSearchParams(search).get('api');
  if (fromQuery) return { mode: 'api', url: fromQuery };
  try {
    const raw = storage?.getItem(CONNECTION_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<{ mode: string; url: string }>;
      if (parsed.mode === 'api' && typeof parsed.url === 'string' && parsed.url) return { mode: 'api', url: parsed.url };
    }
  } catch {
    // Storage blocked or corrupt: fall back to the demo.
  }
  return { mode: 'demo' };
}

export function saveConnection(connection: Connection, storage: StorageLike | null): void {
  try {
    storage?.setItem(CONNECTION_KEY, JSON.stringify(connection));
  } catch {
    // Not fatal: the choice just won't be remembered.
  }
}

/** Files each mode can read. The API adds PDFs and photos. */
export const ACCEPT = {
  demo: '.txt,.csv,text/plain',
  api: '.txt,.csv,.pdf,.png,.jpg,.jpeg,.webp,.gif,text/plain,application/pdf,image/png,image/jpeg,image/webp,image/gif',
} as const;

export const MAX_UPLOAD_BYTES = { demo: 1024 * 1024, api: 10 * 1024 * 1024 } as const;

/** Returns an error message, or null if the file can be uploaded in this mode. */
export function checkUpload(fileName: string, size: number, mode: Connection['mode']): string | null {
  const textOnly = /\.(txt|csv)$/i.test(fileName);
  const apiTypes = /\.(txt|csv|pdf|png|jpe?g|webp|gif)$/i.test(fileName);
  if (mode === 'demo' && !textOnly) {
    return 'The browser demo reads .txt files. Connect to the API to upload PDFs and photos.';
  }
  if (mode === 'api' && !apiTypes) {
    return /\.(heic|tiff?)$/i.test(fileName)
      ? 'Please convert HEIC or TIFF photos to JPG or PNG first.'
      : 'Upload a PDF, a photo (JPG, PNG, WebP) or a .txt file.';
  }
  if (size === 0) return 'That file is empty.';
  if (size > MAX_UPLOAD_BYTES[mode]) return mode === 'demo' ? 'Please choose a file under 1 MB for the demo.' : 'Please choose a file under 10 MB.';
  return null;
}

/** True when a field's source text points at this line of the document. */
export function sourceMatchesLine(source: string | null, line: string): boolean {
  if (!source) return false;
  // Hybrid mode appends what the rules read; that part is not in the document.
  const quote = source.replace(/\s*\[rules read:[^\]]*\]\s*$/, '').replace(/\s+/g, ' ').trim().toLowerCase();
  const text = line.replace(/\s+/g, ' ').trim().toLowerCase();
  if (!quote || !text) return false;
  return text === quote || (quote.length >= 3 && text.includes(quote)) || (text.length >= 6 && quote.includes(text));
}

/** A signed-in session for one API address. Kept in sessionStorage, so it ends when the tab closes. */
export interface StoredSession {
  url: string;
  token: string;
}

export const SESSION_KEY = 'doc-intake-session:v1';

export function loadSession(url: string, storage: StorageLike | null): string | null {
  try {
    const raw = storage?.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredSession>;
    return parsed.url === url && typeof parsed.token === 'string' && parsed.token ? parsed.token : null;
  } catch {
    return null;
  }
}

export function saveSession(session: StoredSession | null, storage: StorageLike | null): void {
  try {
    if (session) storage?.setItem(SESSION_KEY, JSON.stringify(session));
    else storage?.setItem(SESSION_KEY, '');
  } catch {
    // Not fatal: the user signs in again next time.
  }
}
