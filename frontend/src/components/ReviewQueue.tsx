import { useMemo, useState } from 'react';
import type { Client, DocStatus, IntakeDocument } from '../lib/types';
import { Card, ConfidenceBar, StatusBadge, TypeBadge, timeAgo } from './ui';

type Filter = 'to_review' | DocStatus | 'all';

const FILTERS: Array<{ id: Filter; label: string }> = [
  { id: 'to_review', label: 'To review' },
  { id: 'needs_review', label: 'Flagged' },
  { id: 'ready', label: 'Ready' },
  { id: 'approved', label: 'Approved' },
  { id: 'rejected', label: 'Rejected' },
  { id: 'all', label: 'All' },
];

function matches(doc: IntakeDocument, filter: Filter): boolean {
  if (filter === 'all') return true;
  if (filter === 'to_review') return doc.status === 'needs_review' || doc.status === 'ready';
  return doc.status === filter;
}

export function issueCount(doc: IntakeDocument): number {
  return doc.extraction.lowConfidenceFields.length + doc.extraction.checks.filter((c) => !c.passed).length;
}

export function ReviewQueue({
  documents,
  clients,
  onOpen,
}: {
  documents: IntakeDocument[];
  clients: Client[];
  onOpen: (id: string) => void;
}) {
  const [filter, setFilter] = useState<Filter>('to_review');
  const clientName = (id: string) => clients.find((c) => c.id === id)?.name ?? 'Unknown client';

  const rows = useMemo(
    () =>
      documents
        .filter((d) => matches(d, filter))
        .sort((a, b) => {
          // Flagged first, then the lowest confidence, then newest.
          const rank = (d: IntakeDocument) => (d.status === 'needs_review' ? 0 : 1);
          return rank(a) - rank(b) || a.extraction.overallConfidence - b.extraction.overallConfidence || b.uploadedAt.localeCompare(a.uploadedAt);
        }),
    [documents, filter],
  );

  const toReview = documents.filter((d) => matches(d, 'to_review'));
  const flagged = documents.filter((d) => d.status === 'needs_review').length;
  const approved = documents.filter((d) => d.status === 'approved').length;
  const avg = documents.length
    ? Math.round((documents.reduce((s, d) => s + d.extraction.overallConfidence, 0) / documents.length) * 100)
    : 0;

  return (
    <div className="stack">
      <div className="kpis">
        <div className="kpi">
          <span className="kpi__value">{toReview.length}</span>
          <span className="kpi__label">Waiting for review</span>
        </div>
        <div className="kpi kpi--warn">
          <span className="kpi__value">{flagged}</span>
          <span className="kpi__label">Flagged by the AI</span>
        </div>
        <div className="kpi kpi--ok">
          <span className="kpi__value">{approved}</span>
          <span className="kpi__label">Approved</span>
        </div>
        <div className="kpi">
          <span className="kpi__value">{avg}%</span>
          <span className="kpi__label">Average extraction confidence</span>
        </div>
      </div>

      <Card
        title="Review queue"
        actions={
          <div className="segmented" role="tablist" aria-label="Filter documents">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                role="tab"
                aria-selected={filter === f.id}
                className={filter === f.id ? 'is-active' : ''}
                onClick={() => setFilter(f.id)}
              >
                {f.label}
                <span className="count">{documents.filter((d) => matches(d, f.id)).length}</span>
              </button>
            ))}
          </div>
        }
      >
        {rows.length === 0 ? (
          <p className="empty">Nothing here. New uploads from the client portal appear in this queue.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Client</th>
                  <th>Confidence</th>
                  <th>Issues</th>
                  <th>Status</th>
                  <th>Uploaded</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((doc) => {
                  const issues = issueCount(doc);
                  return (
                    <tr key={doc.id} onClick={() => onOpen(doc.id)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onOpen(doc.id)}>
                      <td>
                        <div className="doc-cell">
                          <TypeBadge type={doc.extraction.docType} />
                          <span className="doc-cell__name">{doc.fileName}</span>
                        </div>
                      </td>
                      <td>{clientName(doc.clientId)}</td>
                      <td>
                        <ConfidenceBar value={doc.extraction.overallConfidence} />
                      </td>
                      <td>{issues === 0 ? <span className="muted">None</span> : <span className="issues">{issues} to check</span>}</td>
                      <td>
                        <StatusBadge status={doc.status} />
                      </td>
                      <td className="muted">{timeAgo(doc.uploadedAt)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
