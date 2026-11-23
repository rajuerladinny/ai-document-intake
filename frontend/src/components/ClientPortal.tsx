import { useState, type ChangeEvent } from 'react';
import { TYPE_LABELS } from '../lib/extract';
import { checklist, type DemoState } from '../lib/demoStore';
import { ACCEPT, checkUpload, type Connection } from '../lib/connection';
import { SAMPLES } from '../lib/samples';
import type { IntakeDocument, UploadInput } from '../lib/types';
import { Card, ChecklistChip, TypeBadge, timeAgo } from './ui';

interface Props {
  state: DemoState;
  clientId: string;
  onClientChange: (id: string) => void;
  onUpload: (input: UploadInput) => Promise<IntakeDocument | null>;
  mode: Connection['mode'];
  busy: boolean;
  error: string | null;
  /** A signed-in client user: no switching to other clients. */
  lockedToClient?: boolean;
}

function formatSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function ClientPortal({ state, clientId, onClientChange, onUpload, mode, busy, error, lockedToClient = false }: Props) {
  const [text, setText] = useState('');
  const [fileName, setFileName] = useState('');
  const [last, setLast] = useState<IntakeDocument | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  /** A PDF or photo, sent as-is (API mode). Text files are read into the text box instead. */
  const [binary, setBinary] = useState<File | null>(null);
  const client = state.clients.find((c) => c.id === clientId);
  const items = checklist(state, clientId);
  const docs = state.documents.filter((d) => d.clientId === clientId);
  const done = items.filter((i) => i.state === 'approved').length;

  const pickSample = (name: string) => {
    const sample = SAMPLES.find((s) => s.fileName === name);
    if (!sample) return;
    setText(sample.text);
    setFileName(sample.fileName);
    setBinary(null);
    setFileError(null);
  };

  const pickFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const problem = checkUpload(file.name, file.size, mode);
    if (problem) {
      setFileError(problem);
      return;
    }
    setFileError(null);
    setFileName(file.name);
    if (/\.(txt|csv)$/i.test(file.name)) {
      setBinary(null);
      setText(await file.text());
    } else {
      setBinary(file);
      setText('');
    }
  };

  const clearFile = () => {
    setBinary(null);
    setFileName('');
  };

  const submit = async () => {
    const doc = await onUpload({ fileName: fileName || 'pasted-document.txt', text, file: binary ?? undefined });
    if (doc) {
      setLast(doc);
      setText('');
      setFileName('');
      setBinary(null);
    }
  };

  const ready = binary !== null || text.trim().length > 0;

  return (
    <div className="portal">
      <div className="portal__head">
        <div>
          <p className="eyebrow">Client portal</p>
          <h1>{client?.name}</h1>
          <p className="muted">
            {done} of {items.length} documents approved for this month
          </p>
        </div>
        {!lockedToClient && (
          <label className="select">
            <span>Viewing as</span>
            <select value={clientId} onChange={(e) => onClientChange(e.target.value)}>
              {state.clients.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="portal__grid">
        <Card title="What we still need">
          <ul className="checklist">
            {items.map((item) => (
              <li key={item.docType}>
                <ChecklistChip type={item.docType} state={item.state} />
              </li>
            ))}
          </ul>
        </Card>

        <Card title="Upload a document">
          <div className="upload">
            <div className="upload__options">
              <label className="btn btn--ghost upload__file">
                {mode === 'api' ? 'Choose a PDF, photo or .txt' : 'Choose a .txt file'}
                <input type="file" accept={ACCEPT[mode]} onChange={pickFile} />
              </label>
              <select value="" onChange={(e) => pickSample(e.target.value)} aria-label="Use a sample document">
                <option value="" disabled>
                  …or try a sample document
                </option>
                {SAMPLES.map((s) => (
                  <option key={s.fileName} value={s.fileName}>
                    {s.title}
                  </option>
                ))}
              </select>
            </div>
            {binary ? (
              <div className="upload__picked">
                <span className="upload__icon" aria-hidden="true">
                  {binary.type === 'application/pdf' || /\.pdf$/i.test(binary.name) ? 'PDF' : 'IMG'}
                </span>
                <span>
                  <strong>{binary.name}</strong>
                  <span className="muted"> · {formatSize(binary.size)}</span>
                </span>
                <button className="btn btn--link" onClick={clearFile}>
                  Remove
                </button>
              </div>
            ) : (
              <textarea
                rows={8}
                value={text}
                placeholder="Or paste the text of an invoice, receipt, payslip or bank statement here"
                onChange={(e) => setText(e.target.value)}
                aria-label="Document text"
              />
            )}
            {fileName && !binary && <p className="muted">File: {fileName}</p>}
            {(fileError || error) && (
              <div className="notice notice--error" role="alert">
                {fileError ?? error}
              </div>
            )}
            <button className="btn btn--primary" disabled={!ready || busy} onClick={() => void submit()}>
              {busy ? 'Reading your document…' : 'Send to my accountant'}
            </button>
            {last && (
              <div className="notice notice--ok" role="status">
                Received <strong>{last.fileName}</strong>. We read it as {TYPE_LABELS[last.extraction.docType].toLowerCase()} (
                {Math.round(last.extraction.overallConfidence * 100)}% confidence). Your accountant will review it shortly.
              </div>
            )}
          </div>
        </Card>
      </div>

      <Card title="Your documents">
        {docs.length === 0 ? (
          <p className="empty">You haven’t sent any documents yet.</p>
        ) : (
          <ul className="doc-list">
            {docs.map((d) => (
              <li key={d.id}>
                <TypeBadge type={d.extraction.docType} />
                <span className="doc-list__name">{d.fileName}</span>
                <span className="muted">{timeAgo(d.uploadedAt)}</span>
                <span className={`badge badge--${d.status === 'needs_review' ? 'ready' : d.status}`}>
                  {d.status === 'approved' ? 'Approved' : d.status === 'rejected' ? 'Resend needed' : 'In review'}
                </span>
                {d.status === 'rejected' && <p className="doc-list__reason">Please resend: {d.rejectReason}</p>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
