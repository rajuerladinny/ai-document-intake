import { useState, type ChangeEvent } from 'react';
import { TYPE_LABELS } from '../lib/extract';
import { checklist, type DemoState } from '../lib/demoStore';
import { SAMPLES } from '../lib/samples';
import type { IntakeDocument } from '../lib/types';
import { Card, ChecklistChip, TypeBadge, timeAgo } from './ui';

interface Props {
  state: DemoState;
  clientId: string;
  onClientChange: (id: string) => void;
  onUpload: (fileName: string, text: string) => IntakeDocument | null;
  error: string | null;
}

export function ClientPortal({ state, clientId, onClientChange, onUpload, error }: Props) {
  const [text, setText] = useState('');
  const [fileName, setFileName] = useState('');
  const [last, setLast] = useState<IntakeDocument | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const client = state.clients.find((c) => c.id === clientId);
  const items = checklist(state, clientId);
  const docs = state.documents.filter((d) => d.clientId === clientId);
  const done = items.filter((i) => i.state === 'approved').length;

  const pickSample = (name: string) => {
    const sample = SAMPLES.find((s) => s.fileName === name);
    if (!sample) return;
    setText(sample.text);
    setFileName(sample.fileName);
    setFileError(null);
  };

  const pickFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (!/\.(txt|csv)$/i.test(file.name)) {
      setFileError('The browser demo reads .txt files. PDF upload works through the API (see the README).');
      return;
    }
    if (file.size > 1024 * 1024) {
      setFileError('Please choose a file under 1 MB for the demo.');
      return;
    }
    setFileError(null);
    setText(await file.text());
    setFileName(file.name);
  };

  const submit = () => {
    const doc = onUpload(fileName || 'pasted-document.txt', text);
    if (doc) {
      setLast(doc);
      setText('');
      setFileName('');
    }
  };

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
                Choose a .txt file
                <input type="file" accept=".txt,.csv,text/plain" onChange={pickFile} />
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
            <textarea
              rows={8}
              value={text}
              placeholder="Or paste the text of an invoice, receipt, payslip or bank statement here"
              onChange={(e) => setText(e.target.value)}
              aria-label="Document text"
            />
            {fileName && <p className="muted">File: {fileName}</p>}
            {(fileError || error) && (
              <div className="notice notice--error" role="alert">
                {fileError ?? error}
              </div>
            )}
            <button className="btn btn--primary" disabled={!text.trim()} onClick={submit}>
              Send to my accountant
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
