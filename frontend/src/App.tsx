import { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityView } from './components/ActivityView';
import { ClientPortal } from './components/ClientPortal';
import { ClientsView } from './components/ClientsView';
import { ConnectionSettings, extractorLabel } from './components/ConnectionSettings';
import { ReviewPanel } from './components/ReviewPanel';
import { ReviewQueue } from './components/ReviewQueue';
import { SignIn } from './components/SignIn';
import {
  ActionError,
  approve,
  correctField,
  createDemoState,
  ingest,
  reject,
  type DemoState,
} from './lib/demoStore';
import { ApiClient, ApiError, type Health, type ReminderResult, type SessionUser } from './lib/api';
import { initialConnection, loadSession, saveConnection, saveSession, type Connection } from './lib/connection';
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

function safeSessionStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function downloadText(text: string, fileName: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
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
  const [health, setHealth] = useState<Health | null>(null);
  const [token, setToken] = useState<string | null>(() => {
    const initial = initialConnection(window.location.search, safeStorage());
    return initial.mode === 'api' ? loadSession(initial.url, safeSessionStorage()) : null;
  });
  const [user, setUser] = useState<SessionUser | null>(null);
  const [signInNotice, setSignInNotice] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const base = useMemo((): { client: ApiClient | null; problem: string | null } => {
    if (connection.mode !== 'api') return { client: null, problem: null };
    try {
      return { client: new ApiClient(connection.url), problem: null };
    } catch (e) {
      return { client: null, problem: messageOf(e) };
    }
  }, [connection]);
  const api = useMemo(() => ({ ...base, client: base.client?.withToken(token) ?? null }), [base, token]);
  const apiMode = connection.mode === 'api';
  const state = apiMode ? (apiState ?? EMPTY) : demoState;
  const extractor = health?.extractor ?? null;
  const needsSignIn = apiMode && health?.authRequired === true && !user;
  const isClientUser = apiMode && user?.role === 'client';
  const isAdmin = !apiMode || !health?.authRequired || user?.role === 'admin';

  /** Forget the session (expired, or signed out) and show the sign-in screen. */
  const dropSession = useCallback(
    (notice: string | null) => {
      if (connection.mode === 'api') saveSession(null, safeSessionStorage());
      setToken(null);
      setUser(null);
      setApiState(null);
      setSignInNotice(notice);
    },
    [connection],
  );

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
      const info = await api.client.health();
      setHealth(info);
      setLoadError(null);
      if (info.authRequired && !token) {
        setApiState(null);
        setUser(null);
        return; // the sign-in screen shows
      }
      const [me, loaded] = await Promise.all([info.authRequired ? api.client.me() : Promise.resolve(null), api.client.loadState()]);
      setUser(me);
      setApiState(loaded);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        dropSession('Your session has ended. Please sign in again.');
        return;
      }
      setLoadError(messageOf(e));
    }
  }, [api, token, dropSession]);

  useEffect(() => {
    setApiState(null);
    setLoadError(null);
    setError(null);
    if (apiMode) void refresh();
    else setHealth(null);
  }, [apiMode, refresh]);

  // A client user only has their own portal.
  useEffect(() => {
    if (isClientUser && user?.clientId && (route.view !== 'portal' || route.clientId !== user.clientId)) {
      window.location.hash = toHash({ view: 'portal', clientId: user.clientId });
    }
  }, [isClientUser, user, route]);

  const signIn = async (email: string, password: string): Promise<string | null> => {
    if (!base.client || connection.mode !== 'api') return 'Not connected to an API.';
    try {
      const result = await base.client.login(email, password);
      saveSession({ url: connection.url, token: result.token }, safeSessionStorage());
      setSignInNotice(null);
      setToken(result.token); // triggers refresh with the new token
      setRoute(parseHash(window.location.hash));
      return null;
    } catch (e) {
      return messageOf(e);
    }
  };

  const signOut = async () => {
    try {
      await api.client?.logout();
    } catch {
      // Signing out locally is what matters.
    }
    dropSession(null);
    window.location.hash = '#/queue';
  };

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
    setToken(next.mode === 'api' ? loadSession(next.url, safeSessionStorage()) : null);
    setUser(null);
    setHealth(null);
    setConnection(next);
  };

  /** Turns an API error into a message, signing out if the session has ended. */
  const handleApiError = (e: unknown) => {
    if (e instanceof ApiError && e.status === 401) dropSession('Your session has ended. Please sign in again.');
    else setError(messageOf(e));
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
      handleApiError(e);
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
      const doc = await api.client.upload(clientId, file);
      setApiState(await api.client.loadState());
      return doc;
    } catch (e) {
      handleApiError(e);
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
  const loading = apiMode && apiState === null && !loadError && !needsSignIn;
  const unavailable = apiMode && apiState === null && loadError !== null;

  const sendReminders = async (): Promise<ReminderResult[] | null> => {
    if (!api.client) return null;
    try {
      const results = await api.client.sendReminders();
      setApiState(await api.client.loadState());
      return results;
    } catch (e) {
      handleApiError(e);
      return null;
    }
  };

  const exportAudit = async () => {
    if (!api.client) return;
    try {
      downloadText(await api.client.auditCsv(), `audit-log-${new Date().toISOString().slice(0, 10)}.csv`, 'text/csv');
      setApiState(await api.client.loadState());
    } catch (e) {
      handleApiError(e);
    }
  };

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

        {!isClientUser && !needsSignIn && (
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
        )}

        <ConnectionSettings connection={connection} extractor={extractor} connected={health !== null && !loadError} onChange={changeConnection} />

        {user && (
          <span className="who">
            <span className="who__name">{user.name}</span>
            <span className="who__role">{user.role}</span>
          </span>
        )}

        {apiMode ? (
          user ? (
            <button className="btn btn--ghost btn--small" onClick={() => void signOut()}>
              Sign out
            </button>
          ) : (
            !needsSignIn && (
              <button className="btn btn--ghost btn--small" onClick={() => void refresh()} title="Load the latest documents">
                Refresh
              </button>
            )
          )
        ) : (
          <button className="btn btn--ghost btn--small" onClick={reset} title="Restore the sample data">
            Reset demo
          </button>
        )}
      </header>

      {apiMode ? (
        !loadError && (
        <div className="demo-note demo-note--api">
          {apiState || needsSignIn ? 'Connected to' : 'Connecting to'} the API at {connection.url}
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

      {needsSignIn && connection.mode === 'api' && (
        <SignIn
          apiUrl={connection.url}
          demoAccounts={health?.demoAccounts}
          notice={signInNotice}
          onSignIn={signIn}
          onUseDemo={() => changeConnection({ mode: 'demo' })}
        />
      )}

      {isReviewer && !unavailable && !needsSignIn && !isClientUser && (
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

      <main className="main" aria-busy={busy || loading} hidden={unavailable || needsSignIn}>
        {loading && <p className="empty">Loading documents from the API…</p>}
        {route.view === 'queue' && !isClientUser && <ReviewQueue documents={state.documents} clients={state.clients} onOpen={(id) => go({ view: 'review', id })} />}

        {route.view === 'review' &&
          (current ? (
            <ReviewPanel
              key={current.id}
              doc={current}
              client={state.clients.find((c) => c.id === current.clientId)}
              error={error}
              busy={busy}
              onBack={() => go({ view: 'queue' })}
              onCorrect={(key, value) => void perform((s) => correctField(s, current.id, key, value, REVIEWER), (c) => c.correctField(current.id, key, value))}
              onApprove={() => void perform((s) => approve(s, current.id, REVIEWER), (c) => c.approve(current.id))}
              onReject={(reason) => void perform((s) => reject(s, current.id, reason, REVIEWER), (c) => c.reject(current.id, reason))}
              onNext={nextAfter(current.id) ? () => go({ view: 'review', id: (nextAfter(current.id) as IntakeDocument).id }) : null}
            />
          ) : (
            !loading && (
              <p className="empty">
                That document isn’t here any more. <a href="#/queue">Back to the queue</a>
              </p>
            )
          ))}

        {route.view === 'clients' && !isClientUser && (
          <ClientsView state={state} onSendReminders={apiMode && isAdmin ? sendReminders : undefined} />
        )}

        {route.view === 'activity' && !isClientUser && (
          <ActivityView
            activity={state.activity}
            documents={state.documents}
            onOpen={(id) => go({ view: 'review', id })}
            onExport={apiMode && isAdmin ? () => void exportAudit() : undefined}
          />
        )}

        {route.view === 'portal' && !loading && state.clients.length > 0 && (
          <ClientPortal
            state={state}
            clientId={portalClientId}
            mode={connection.mode}
            busy={busy}
            error={error}
            lockedToClient={isClientUser}
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
