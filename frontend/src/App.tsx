import { useCallback, useEffect, useState } from 'react';
import { ActivityView } from './components/ActivityView';
import { ClientPortal } from './components/ClientPortal';
import { ClientsView } from './components/ClientsView';
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
import { DEMO_CLIENTS, SAMPLES } from './lib/samples';
import type { IntakeDocument } from './lib/types';

const STORAGE_KEY = 'doc-intake-demo:v1';
const REVIEWER = 'ana.reviewer@demo';

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
  const [state, setState] = useState<DemoState>(loadState);
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      // Not fatal: the demo still works for this visit.
    }
  }, [state]);

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

  const attempt = (action: (s: DemoState) => DemoState): boolean => {
    try {
      setState(action(state));
      setError(null);
      return true;
    } catch (e) {
      setError(e instanceof ActionError ? e.message : 'Something went wrong.');
      return false;
    }
  };

  const toReview = state.documents
    .filter((d) => d.status === 'needs_review' || d.status === 'ready')
    .sort((a, b) => (a.status === b.status ? a.extraction.overallConfidence - b.extraction.overallConfidence : a.status === 'needs_review' ? -1 : 1));
  const nextAfter = (id: string) => toReview.find((d) => d.id !== id) ?? null;

  const reset = () => {
    setState(freshState());
    setError(null);
    go({ view: 'queue' });
  };

  const isReviewer = route.view !== 'portal';
  const current: IntakeDocument | undefined = route.view === 'review' ? state.documents.find((d) => d.id === route.id) : undefined;

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
            onClick={() => go({ view: 'portal', clientId: DEMO_CLIENTS[0].id })}
          >
            Client portal
          </button>
        </div>

        <button className="btn btn--ghost btn--small" onClick={reset} title="Restore the sample data">
          Reset demo
        </button>
      </header>

      <div className="demo-note">
        Live demo: everything runs in your browser with sample documents. Nothing is uploaded anywhere.
      </div>

      {isReviewer && (
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

      <main className="main">
        {route.view === 'queue' && <ReviewQueue documents={state.documents} clients={state.clients} onOpen={(id) => go({ view: 'review', id })} />}

        {route.view === 'review' &&
          (current ? (
            <ReviewPanel
              key={current.id}
              doc={current}
              client={state.clients.find((c) => c.id === current.clientId)}
              error={error}
              onBack={() => go({ view: 'queue' })}
              onCorrect={(key, value) => attempt((s) => correctField(s, current.id, key, value, REVIEWER))}
              onApprove={() => attempt((s) => approve(s, current.id, REVIEWER))}
              onReject={(reason) => attempt((s) => reject(s, current.id, reason, REVIEWER))}
              onNext={nextAfter(current.id) ? () => go({ view: 'review', id: (nextAfter(current.id) as IntakeDocument).id }) : null}
            />
          ) : (
            <p className="empty">
              That document isn’t in this demo any more. <a href="#/queue">Back to the queue</a>
            </p>
          ))}

        {route.view === 'clients' && <ClientsView state={state} />}

        {route.view === 'activity' && <ActivityView activity={state.activity} documents={state.documents} onOpen={(id) => go({ view: 'review', id })} />}

        {route.view === 'portal' && (
          <ClientPortal
            state={state}
            clientId={route.clientId}
            error={error}
            onClientChange={(clientId) => go({ view: 'portal', clientId })}
            onUpload={(fileName, text) => {
              try {
                const result = ingest(state, route.clientId, fileName, text, state.clients.find((c) => c.id === route.clientId)?.contactEmail ?? 'client');
                setState(result.state);
                setError(null);
                return result.doc;
              } catch (e) {
                setError(e instanceof ActionError ? e.message : 'Something went wrong.');
                return null;
              }
            }}
          />
        )}
      </main>

      <footer className="footer">
        <span>Rule-based extraction in the demo · swap in an LLM extractor through the API</span>
        <a href="https://github.com/rajuerladinny/ai-document-intake">Source on GitHub</a>
      </footer>
    </div>
  );
}
