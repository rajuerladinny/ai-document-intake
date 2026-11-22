import { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityView } from './components/ActivityView';
import { ClientPortal } from './components/ClientPortal';
import { ClientsView } from './components/ClientsView';
import { ConnectionSettings, extractorLabel } from './components/ConnectionSettings';
import { ReviewPanel } from './components/ReviewPanel';
import { ReviewQueue } from './components/ReviewQueue';
import {
  ActionError,
  approve,
  correctField,
  createDemoState,
  ingest,
  reject,
  type DemoState,
} from './lib/demoStore';
import { ApiClient, ApiError } from './lib/api';
import { initialConnection, saveConnection, type Connection } from './lib/connection';
import { DEMO_CLIENTS, SAMPLES } from './lib/samples';
import type { IntakeDocument, UploadInput } from './lib/types';

const STORAGE_KEY = 'doc-intake-demo:v1';
const REVIEWER = 'ana.reviewer@demo';
const EMPTY: DemoState = { clients: [], documents: [], activity: [] };

function safeStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function messageOf(e: unknown): string {
  return e instanceof ActionError || e instanceof ApiError ? e.message : 'Something went wrong.';
}

type Route =
  | { view: 'queue' }
  | { view: 'review'; id: string }
  | { view: 'clients' }
  | { view: 'activity' }
  | { view: 'portal'; clientId: string };

function parseHash(hash: string): Route {
  const [, view, arg] = hash.replace(/^#/, '').split('/');
  if (view === 'review' && arg) return { view: 'review', id: arg };
  if (view === 'clients') return { view: 'clients' };
  if (view === 'activity') return { view: 'activity' };
  if (view === 'portal') return { view: 'portal', clientId: arg || DEMO_CLIENTS[0].id };
  return { view: 'queue' };
}

function toHash(route: Route): string {
  switch (route.view) {
    case 'review':
      return `#/review/${route.id}`;
    case 'portal':
      return `#/portal/${route.clientId}`;
    default:
      return `#/${route.view}`;
  }
}

function freshState(): DemoState {
  return createDemoState(DEMO_CLIENTS, Object.fromEntries(SAMPLES.map((s) => [s.fileName, s.text])));
}

function loadState(): DemoState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as DemoState;
      if (Array.isArray(parsed.documents) && Array.isArray(parsed.clients)) return parsed;
    }
  } catch {
    // Storage can be unavailable (private mode, blocked cookies). Start fresh.
  }
  return freshState();
}

export function App() {
  const [connection, setConnection] = useState<Connection>(() => initialConnection(window.location.search, safeStorage()));
  const [demoState, setDemoState] = useState<DemoState>(loadState);
  const [apiState, setApiState] = useState<DemoState | null>(null);
  const [extractor, setExtractor] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const api = useMemo((): { client: ApiClient | null; problem: string | null } => {
    if (connection.mode !== 'api') return { client: null, problem: null };
    try {
      return { client: new ApiClient(connection.url), problem: null };
    } catch (e) {
      return { client: null, problem: messageOf(e) };
    }
  }, [connection]);
  const apiMode = connection.mode === 'api';
  const state = apiMode ? (apiState ?? EMPTY) : demoState;

  useEffect(() => {
    if (apiMode) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(demoState));
    } catch {
      // Not fatal: the demo still works for this visit.
    }
  }, [demoState, apiMode]);

  const refresh = useCallback(async () => {
    if (!api.client) {
      setLoadError(api.problem);
      return;
    }
    try {
      const [health, loaded] = await Promise.all([api.client.health(), api.client.loadState()]);
      setExtractor(health.extractor);
      setApiState(loaded);
      setLoadError(null);
    } catch (e) {
      setLoadError(messageOf(e));
    }
  }, [api]);

  useEffect(() => {
    setApiState(null);
    setExtractor(null);
    setLoadError(null);
    setError(null);
    if (apiMode) void refresh();
  }, [apiMode, refresh]);

  useEffect(() => {
    const onHash = () => {
      setRoute(parseHash(window.location.hash));
      setError(null);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const go = useCallback((next: Route) => {
    window.location.hash = toHash(next);
  }, []);

  const changeConnection = (next: Connection) => {
    saveConnection(next, safeStorage());
    setConnection(next);
  };

  /** Runs a change against the demo state or the API, and reports errors. */
  const perform = async (
    demo: (s: DemoState) => DemoState,
    remote: (client: ApiClient) => Promise<unknown>,
  ): Promise<boolean> => {
    setError(null);
    if (!apiMode) {
      try {
        setDemoState(demo(demoState));
        return true;
      } catch (e) {
        setError(messageOf(e));
        return false;
      }
    }
    if (!api.client || busy) return false;
    setBusy(true);
    try {
      await remote(api.client);
      setApiState(await api.client.loadState());
      return true;
    } catch (e) {
      setError(messageOf(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const upload = async (clientId: string, input: UploadInput): Promise<IntakeDocument | null> => {
    const actor = state.clients.find((c) => c.id === clientId)?.contactEmail ?? 'client';
    setError(null);
    if (!apiMode) {
      try {
        const result = ingest(demoState, clientId, input.fileName, input.text, actor);
        setDemoState(result.state);
        return result.doc;
      } catch (e) {
        setError(messageOf(e));
        return null;
      }
    }
    if (!api.client) return null;
    setBusy(true);
    try {
      const file = input.file ?? new File([input.text], input.fileName, { type: 'text/plain' });
      const doc = await api.client.upload(clientId, file, actor);
      setApiState(await api.client.loadState());
      return doc;
    } catch (e) {
      setError(messageOf(e));
      return null;
    } finally {
      setBusy(false);
    }
  };

  const toReview = state.documents
    .filter((d) => d.status === 'needs_review' || d.status === 'ready')
    .sort((a, b) => (a.status === b.status ? a.extraction.overallConfidence - b.extraction.overallConfidence : a.status === 'needs_review' ? -1 : 1));
  const nextAfter = (id: string) => toReview.find((d) => d.id !== id) ?? null;

  const reset = () => {
    setDemoState(freshState());
    setError(null);
    go({ view: 'queue' });
  };

  const isReviewer = route.view !== 'portal';
  const current: IntakeDocument | undefined = route.view === 'review' ? state.documents.find((d) => d.id === route.id) : undefined;
  const firstClientId = state.clients[0]?.id ?? DEMO_CLIENTS[0].id;
  const portalClientId =
    route.view === 'portal' && state.clients.some((c) => c.id === route.clientId) ? route.clientId : firstClientId;
  const loading = apiMode && apiState === null && !loadError;
  const unavailable = apiMode && apiState === null && loadError !== null;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true">
            DI
          </span>
          <div>
            <p className="brand__name">DocIntake</p>
            <p className="brand__tag">AI document intake &amp; review</p>
          </div>
        </div>

        <div className="segmented segmented--roles" role="tablist" aria-label="Switch role">
          <button role="tab" aria-selected={isReviewer} className={isReviewer ? 'is-active' : ''} onClick={() => go({ view: 'queue' })}>
            Accountant
          </button>
          <button
            role="tab"
            aria-selected={!isReviewer}
            className={!isReviewer ? 'is-active' : ''}
            onClick={() => go({ view: 'portal', clientId: firstClientId })}
          >
            Client portal
          </button>
        </div>

        <ConnectionSettings connection={connection} extractor={extractor} connected={apiState !== null && !loadError} onChange={changeConnection} />

        {apiMode ? (
          <button className="btn btn--ghost btn--small" onClick={() => void refresh()} title="Load the latest documents">
            Refresh
          </button>
        ) : (
          <button className="btn btn--ghost btn--small" onClick={reset} title="Restore the sample data">
            Reset demo
          </button>
        )}
      </header>

      {apiMode ? (
        !loadError && (
        <div className="demo-note demo-note--api">
          {apiState ? 'Connected to' : 'Connecting to'} the API at {connection.url}
          {extractor && ` · extraction: ${extractorLabel(extractor)}`}. Documents are stored on that server.
        </div>
        )
      ) : (
        <div className="demo-note">
          Live demo: everything runs in your browser with sample documents. Nothing is uploaded anywhere.
        </div>
      )}

      {loadError && (
        <div className="notice notice--error notice--bar" role="alert">
          <span>{loadError}</span>
          <span className="notice__actions">
            <button className="btn btn--ghost btn--small" onClick={() => void refresh()}>
              Try again
            </button>
            <button className="btn btn--link" onClick={() => changeConnection({ mode: 'demo' })}>
              Back to the browser demo
            </button>
          </span>
        </div>
      )}

      {isReviewer && !unavailable && (
        <nav className="tabs" aria-label="Accountant views">
          <a href="#/queue" className={route.view === 'queue' || route.view === 'review' ? 'is-active' : ''}>
            Review queue
          </a>
          <a href="#/clients" className={route.view === 'clients' ? 'is-active' : ''}>
            Clients &amp; reminders
          </a>
          <a href="#/activity" className={route.view === 'activity' ? 'is-active' : ''}>
            Activity history
          </a>
        </nav>
      )}

      <main className="main" aria-busy={busy || loading} hidden={unavailable}>
        {loading && <p className="empty">Loading documents from the API…</p>}
        {route.view === 'queue' && <ReviewQueue documents={state.documents} clients={state.clients} onOpen={(id) => go({ view: 'review', id })} />}

        {route.view === 'review' &&
          (current ? (
            <ReviewPanel
              key={current.id}
              doc={current}
              client={state.clients.find((c) => c.id === current.clientId)}
              error={error}
              busy={busy}
              onBack={() => go({ view: 'queue' })}
              onCorrect={(key, value) => void perform((s) => correctField(s, current.id, key, value, REVIEWER), (c) => c.correctField(current.id, key, value, REVIEWER))}
              onApprove={() => void perform((s) => approve(s, current.id, REVIEWER), (c) => c.approve(current.id, REVIEWER))}
              onReject={(reason) => void perform((s) => reject(s, current.id, reason, REVIEWER), (c) => c.reject(current.id, reason, REVIEWER))}
              onNext={nextAfter(current.id) ? () => go({ view: 'review', id: (nextAfter(current.id) as IntakeDocument).id }) : null}
            />
          ) : (
            !loading && (
              <p className="empty">
                That document isn’t here any more. <a href="#/queue">Back to the queue</a>
              </p>
            )
          ))}

        {route.view === 'clients' && <ClientsView state={state} />}

        {route.view === 'activity' && <ActivityView activity={state.activity} documents={state.documents} onOpen={(id) => go({ view: 'review', id })} />}

        {route.view === 'portal' && !loading && state.clients.length > 0 && (
          <ClientPortal
            state={state}
            clientId={portalClientId}
            mode={connection.mode}
            busy={busy}
            error={error}
            onClientChange={(clientId) => go({ view: 'portal', clientId })}
            onUpload={(input) => upload(portalClientId, input)}
          />
        )}
      </main>

      <footer className="footer">
        <span>{apiMode ? (extractor ? `Extraction: ${extractorLabel(extractor)}` : 'Not connected') : 'Rule-based extraction in the demo · connect the API for PDFs, photos and AI extraction'}</span>
        <a href="https://github.com/rajuerladinny/ai-document-intake">Source on GitHub</a>
      </footer>
    </div>
  );
}
