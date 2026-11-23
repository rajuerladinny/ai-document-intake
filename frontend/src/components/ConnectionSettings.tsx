import { useEffect, useRef, useState } from 'react';
import { ApiClient, ApiError } from '../lib/api';
import type { Connection } from '../lib/connection';

interface Props {
  connection: Connection;
  /** Extractor reported by the API's /health, once connected. */
  extractor: string | null;
  /** True once the API has answered. */
  connected: boolean;
  onChange: (next: Connection) => void;
}

const DEFAULT_URL = 'http://localhost:8000';

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

export function extractorLabel(extractor: string | null | undefined): string {
  if (!extractor) return 'Rules';
  if (extractor.includes('AI unavailable')) return 'Rules (AI was unavailable)';
  const [mode, model] = extractor.split(':');
  if (mode === 'llm') return `AI · ${model}`;
  if (mode === 'hybrid') return `AI + rules · ${model}`;
  return 'Rules';
}

export function ConnectionSettings({ connection, extractor, connected, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Connection['mode']>(connection.mode);
  const [url, setUrl] = useState(connection.mode === 'api' ? connection.url : DEFAULT_URL);
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setMode(connection.mode);
    if (connection.mode === 'api') setUrl(connection.url);
    setProblem(null);
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    const onClick = (e: MouseEvent) => panel.current && !panel.current.contains(e.target as Node) && setOpen(false);
    window.addEventListener('keydown', onKey);
    window.addEventListener('mousedown', onClick);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('mousedown', onClick);
    };
  }, [open, connection]);

  const apply = async () => {
    if (mode === 'demo') {
      onChange({ mode: 'demo' });
      setOpen(false);
      return;
    }
    setChecking(true);
    setProblem(null);
    try {
      const client = new ApiClient(url);
      await client.health();
      onChange({ mode: 'api', url: client.baseUrl });
      setOpen(false);
    } catch (e) {
      setProblem(e instanceof ApiError ? e.message : 'Could not connect.');
    } finally {
      setChecking(false);
    }
  };

  const label = connection.mode === 'demo' ? 'Demo data' : `API · ${hostOf(connection.url)}`;

  return (
    <div className="conn" ref={panel}>
      <button
        className={`btn btn--ghost btn--small conn__toggle ${connection.mode === 'api' ? (connected ? 'is-live' : 'is-down') : ''}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((o) => !o)}
        title="Choose where documents are stored and read"
      >
        <span className="conn__dot" aria-hidden="true" />
        {label}
      </button>
      {open && (
        <div className="conn__panel" role="dialog" aria-label="Data source">
          <p className="conn__title">Data source</p>
          <label className="conn__option">
            <input type="radio" name="conn-mode" checked={mode === 'demo'} onChange={() => setMode('demo')} />
            <span>
              <strong>Browser demo</strong>
              <span className="muted">Sample data in your browser. Text files only, rule-based extraction.</span>
            </span>
          </label>
          <label className="conn__option">
            <input type="radio" name="conn-mode" checked={mode === 'api'} onChange={() => setMode('api')} />
            <span>
              <strong>Connect to the API</strong>
              <span className="muted">PDFs and photos, AI extraction if the API is set up for it.</span>
            </span>
          </label>
          {mode === 'api' && (
            <label className="conn__url">
              <span>API address</span>
              <input
                type="url"
                value={url}
                placeholder={DEFAULT_URL}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && void apply()}
                autoFocus
              />
            </label>
          )}
          {connection.mode === 'api' && connected && extractor && (
            <p className="muted conn__meta">Connected · extractor: {extractorLabel(extractor)}</p>
          )}
          {problem && (
            <div className="notice notice--error" role="alert">
              {problem}
            </div>
          )}
          <div className="conn__actions">
            <button className="btn btn--ghost btn--small" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button className="btn btn--primary btn--small" onClick={() => void apply()} disabled={checking}>
              {checking ? 'Checking…' : mode === 'api' ? 'Connect' : 'Use demo'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
