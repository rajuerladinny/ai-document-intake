import { useState } from 'react';
import { TYPE_LABELS } from '../lib/extract';
import { sourceMatchesLine } from '../lib/connection';
import { missingRequired } from '../lib/demoStore';
import type { Client, ExtractedField, IntakeDocument } from '../lib/types';
import { extractorLabel } from './ConnectionSettings';
import { ConfidenceChip, StatusBadge, TypeBadge, formatDateTime, maskSensitive } from './ui';

interface Props {
  doc: IntakeDocument;
  client: Client | undefined;
  onBack: () => void;
  onCorrect: (key: string, value: string) => void;
  onApprove: () => void;
  onReject: (reason: string) => void;
  onNext: (() => void) | null;
  error: string | null;
  busy?: boolean;
}

const RULES_READ = /\s*\[rules read: ([^\]]*)\]\s*$/;

/** The original upload: a photo or PDF shown as-is, next to the text that was read from it. */
function OriginalFile({ file, fileName }: { file: NonNullable<IntakeDocument['file']>; fileName: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <p className="empty">
        The original file couldn’t be loaded. <a href={file.url} target="_blank" rel="noreferrer">Open it in a new tab</a>
      </p>
    );
  }
  if (file.mediaType === 'application/pdf') {
    return <iframe className="original original--pdf" src={file.url} title={`Original PDF: ${fileName}`} />;
  }
  return (
    <div className="original">
      <img src={file.url} alt={`Original upload: ${fileName}`} onError={() => setFailed(true)} />
    </div>
  );
}

function FieldRow({
  field,
  locked,
  onSave,
  onFocus,
}: {
  field: ExtractedField;
  locked: boolean;
  onSave: (value: string) => void;
  onFocus: (source: string | null) => void;
}) {
  const [draft, setDraft] = useState(field.value ?? '');
  const dirty = draft.trim() !== (field.value ?? '');
  const corrected = field.source?.startsWith('Corrected by') ?? false;
  return (
    <div
      className={`field ${field.value === null && field.required ? 'field--missing' : ''}`}
      onMouseEnter={() => onFocus(field.source)}
      onMouseLeave={() => onFocus(null)}
    >
      <label className="field__label" htmlFor={`f-${field.key}`}>
        {field.label}
        {field.required && <span className="field__req" title="Required">*</span>}
      </label>
      <div className="field__row">
        <input
          id={`f-${field.key}`}
          className="field__input"
          value={draft}
          placeholder={field.value === null ? 'Not found — type the value' : ''}
          disabled={locked}
          onFocus={() => onFocus(field.source)}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => dirty && onSave(draft)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && dirty) onSave(draft);
            if (e.key === 'Escape') setDraft(field.value ?? '');
          }}
        />
        {corrected ? <span className="chip chip--corrected">Corrected</span> : <ConfidenceChip value={field.confidence} hasValue={field.value !== null} />}
      </div>
      {field.source && (
        <p className="field__source">
          {field.source === '(masked)'
            ? 'Masked: only the last 4 digits are stored'
            : field.source.startsWith('Corrected by')
              ? field.source
              : `From: “${maskSensitive(field.source.replace(RULES_READ, ''))}”`}
        </p>
      )}
      {field.source && RULES_READ.test(field.source) && (
        <p className="field__disagree">AI and rules disagree. Rules read: {maskSensitive(field.source.match(RULES_READ)?.[1] ?? '')}</p>
      )}
    </div>
  );
}

export function ReviewPanel({ doc, client, onBack, onCorrect, onApprove, onReject, onNext, error, busy = false }: Props) {
  const [highlight, setHighlight] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const locked = doc.status === 'approved' || doc.status === 'rejected';
  const { extraction } = doc;
  const missing = missingRequired(doc);
  const failedChecks = extraction.checks.filter((c) => !c.passed);
  const lines = doc.text.split(/\r?\n/);
  const visual = doc.file && doc.file.mediaType !== 'text/plain' ? doc.file : null;
  const [view, setView] = useState<'original' | 'text'>(visual ? 'original' : 'text');
  const showOriginal = visual !== null && view === 'original';

  return (
    <div className="review">
      <div className="review__bar">
        <button className="btn btn--ghost" onClick={onBack}>
          ← Back to queue
        </button>
        <div className="review__title">
          <h1>{doc.fileName}</h1>
          <p className="muted">
            {client?.name ?? 'Unknown client'} · uploaded {formatDateTime(doc.uploadedAt)}
          </p>
        </div>
        <StatusBadge status={doc.status} />
      </div>

      <div className="review__grid">
        <section className="paper-wrap" aria-label="Original document">
          <div className="paper-wrap__head">
            {visual ? (
              <div className="segmented segmented--small" role="tablist" aria-label="Document view">
                <button role="tab" aria-selected={showOriginal} className={showOriginal ? 'is-active' : ''} onClick={() => setView('original')}>
                  Original file
                </button>
                <button role="tab" aria-selected={!showOriginal} className={!showOriginal ? 'is-active' : ''} onClick={() => setView('text')}>
                  Text read
                </button>
              </div>
            ) : (
              <span>Original document</span>
            )}
            <span className="muted">{showOriginal ? 'Shown exactly as uploaded' : 'Hover a field to see where it came from'}</span>
          </div>
          {showOriginal && visual ? (
            <OriginalFile file={visual} fileName={doc.fileName} />
          ) : (
            <pre className="paper">
              {lines.map((line, i) => (
                <span key={i} className={sourceMatchesLine(highlight, line) ? 'paper__line is-hit' : 'paper__line'}>
                  {maskSensitive(line) || ' '}
                  {'\n'}
                </span>
              ))}
            </pre>
          )}
        </section>

        <section className="panel" aria-label="Extracted data">
          <div className="summary">
            <div>
              <span className="summary__label">Detected as</span>
              <TypeBadge type={extraction.docType} />
              <span className="muted"> {Math.round(extraction.typeConfidence * 100)}% sure</span>
            </div>
            <div>
              <span className="summary__label">Overall confidence</span>
              <strong>{Math.round(extraction.overallConfidence * 100)}%</strong>
            </div>
            {doc.extractor && (
              <div>
                <span className="summary__label">Read by</span>
                <span className="muted">{extractorLabel(doc.extractor)}</span>
              </div>
            )}
          </div>

          {extraction.docType === 'unknown' ? (
            <div className="notice notice--warn">
              This doesn’t look like an invoice, receipt, payslip or bank statement. Reject it and ask the client for the right document.
            </div>
          ) : (
            <>
              {extraction.needsReview && !locked && (
                <div className="notice notice--warn">
                  {failedChecks.length > 0 && `${failedChecks.length} check${failedChecks.length > 1 ? 's' : ''} failed. `}
                  {extraction.lowConfidenceFields.length > 0 &&
                    `${extraction.lowConfidenceFields.length} field${extraction.lowConfidenceFields.length > 1 ? 's' : ''} need${
                      extraction.lowConfidenceFields.length > 1 ? '' : 's'
                    } a look.`}
                  {failedChecks.length === 0 && extraction.lowConfidenceFields.length === 0 && 'Fields were corrected — check and approve.'}
                </div>
              )}

              <h3 className="panel__heading">Extracted fields</h3>
              <div className="fields">
                {extraction.fields.map((f) => (
                  <FieldRow
                    key={`${doc.id}-${f.key}-${f.value ?? ''}`}
                    field={f}
                    locked={locked}
                    onSave={(v) => onCorrect(f.key, v)}
                    onFocus={setHighlight}
                  />
                ))}
              </div>

              {extraction.checks.length > 0 && (
                <>
                  <h3 className="panel__heading">Checks</h3>
                  <ul className="checks">
                    {extraction.checks.map((c) => (
                      <li key={c.id} className={c.passed ? 'checks__item is-ok' : 'checks__item is-fail'}>
                        <span className="checks__icon" aria-hidden="true">
                          {c.passed ? '✓' : '!'}
                        </span>
                        <div>
                          <strong>{c.label}</strong>
                          <p className="muted">{c.detail}</p>
                        </div>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}

          {error && (
            <div className="notice notice--error" role="alert">
              {error}
            </div>
          )}

          {locked ? (
            <div className={`notice ${doc.status === 'approved' ? 'notice--ok' : 'notice--error'}`}>
              {doc.status === 'approved'
                ? `Approved by ${doc.reviewedBy} · ${formatDateTime(doc.reviewedAt ?? doc.uploadedAt)}`
                : `Rejected by ${doc.reviewedBy}: “${doc.rejectReason}”. The client sees this reason in their portal.`}
              {onNext && (
                <button className="btn btn--link" onClick={onNext}>
                  Next document →
                </button>
              )}
            </div>
          ) : rejecting ? (
            <div className="reject">
              <label htmlFor="reason" className="field__label">
                Reason (shown to the client)
              </label>
              <textarea
                id="reason"
                rows={2}
                value={reason}
                placeholder="e.g. Page 2 is missing, please upload the full statement"
                onChange={(e) => setReason(e.target.value)}
              />
              <div className="actions">
                <button className="btn btn--danger" onClick={() => onReject(reason)} disabled={busy}>
                  Reject document
                </button>
                <button className="btn btn--ghost" onClick={() => setRejecting(false)}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="actions">
              <button
                className="btn btn--primary"
                onClick={onApprove}
                disabled={extraction.docType === 'unknown' || busy}
                title={missing.length ? `Missing: ${missing.join(', ')}` : `Approve this ${TYPE_LABELS[extraction.docType].toLowerCase()}`}
              >
                Approve
              </button>
              <button className="btn btn--ghost" onClick={() => setRejecting(true)}>
                Reject…
              </button>
              {onNext && (
                <button className="btn btn--link" onClick={onNext}>
                  Skip →
                </button>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
