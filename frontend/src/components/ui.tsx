import type { ReactNode } from 'react';
import { REVIEW_THRESHOLD, TYPE_LABELS } from '../lib/extract';
import type { DocStatus, DocType } from '../lib/types';
import type { ChecklistState } from '../lib/demoStore';

export const STATUS_LABELS: Record<DocStatus, string> = {
  needs_review: 'Needs review',
  ready: 'Ready to approve',
  approved: 'Approved',
  rejected: 'Rejected',
};

export function StatusBadge({ status }: { status: DocStatus }) {
  return <span className={`badge badge--${status}`}>{STATUS_LABELS[status]}</span>;
}

export function TypeBadge({ type }: { type: DocType }) {
  return <span className={`type type--${type}`}>{TYPE_LABELS[type]}</span>;
}

export type ConfidenceLevel = 'high' | 'medium' | 'low' | 'missing';

export function confidenceLevel(value: number, hasValue = true): ConfidenceLevel {
  if (!hasValue) return 'missing';
  if (value >= REVIEW_THRESHOLD) return 'high';
  if (value >= 0.6) return 'medium';
  return 'low';
}

const LEVEL_LABELS: Record<ConfidenceLevel, string> = {
  high: 'High',
  medium: 'Check',
  low: 'Low',
  missing: 'Missing',
};

export function ConfidenceChip({ value, hasValue = true }: { value: number; hasValue?: boolean }) {
  const level = confidenceLevel(value, hasValue);
  return (
    <span className={`chip chip--${level}`} title={hasValue ? `${Math.round(value * 100)}% confident` : 'Not found in the document'}>
      {LEVEL_LABELS[level]}
      {hasValue && <span className="chip__pct">{Math.round(value * 100)}%</span>}
    </span>
  );
}

export function ConfidenceBar({ value }: { value: number }) {
  const level = confidenceLevel(value);
  return (
    <span className="bar" aria-label={`${Math.round(value * 100)}% confidence`}>
      <span className={`bar__fill bar__fill--${level}`} style={{ width: `${Math.round(value * 100)}%` }} />
      <span className="bar__label">{Math.round(value * 100)}%</span>
    </span>
  );
}

const CHECKLIST_LABELS: Record<ChecklistState, string> = {
  approved: 'Approved',
  in_review: 'In review',
  rejected: 'Resend needed',
  missing: 'Missing',
};

export function ChecklistChip({ type, state }: { type: DocType; state: ChecklistState }) {
  return (
    <span className={`check-chip check-chip--${state}`}>
      <span className="check-chip__dot" aria-hidden="true" />
      {TYPE_LABELS[type]}
      <span className="check-chip__state">{CHECKLIST_LABELS[state]}</span>
    </span>
  );
}

export function Card({ title, actions, children, className = '' }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card__header">
          {title && <h2 className="card__title">{title}</h2>}
          {actions && <div className="card__actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function timeAgo(iso: string, now: Date = new Date()): string {
  const diff = Math.max(0, now.getTime() - new Date(iso).getTime());
  const minutes = Math.round(diff / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

/** Hides account and National Insurance numbers in the document viewer. */
export function maskSensitive(line: string): string {
  return line
    .replace(/(\baccount(?:\s+number)?\s*:?\s*)\d+(\d{4})\b/gi, '$1****$2')
    .replace(/(\bNI\s+number\s*:?\s*)\S.*$/i, '$1•• •• •• •• (hidden)');
}
