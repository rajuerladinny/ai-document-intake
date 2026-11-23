import type { ActivityEntry, IntakeDocument } from '../lib/types';
import { Card, formatDateTime } from './ui';

const ICONS: Record<string, string> = {
  uploaded: '↑',
  extracted: '✦',
  corrected: '✎',
  approved: '✓',
  rejected: '✕',
  reminder_sent: '✉',
  signed_in: '→',
  audit_exported: '⇩',
  user_created: '+',
  client_created: '+',
};

/** "reminder_sent" → "reminder sent" */
const label = (action: string) => action.replace(/_/g, ' ');

export function ActivityView({
  activity,
  documents,
  onOpen,
  onExport,
}: {
  activity: ActivityEntry[];
  documents: IntakeDocument[];
  onOpen: (id: string) => void;
  /** Admins in API mode: download the full audit log as CSV. */
  onExport?: () => void;
}) {
  const nameOf = (id?: string) => documents.find((d) => d.id === id)?.fileName;
  return (
    <Card
      title="Activity history"
      actions={
        <span className="card__actions-row">
          <span className="muted">{activity.length} events</span>
          {onExport && (
            <button className="btn btn--ghost btn--small" onClick={onExport}>
              Export audit log (CSV)
            </button>
          )}
        </span>
      }
    >
      <ol className="timeline">
        {activity.map((a) => (
          <li key={a.id} className={`timeline__item timeline__item--${a.action}`}>
            <span className="timeline__icon" aria-hidden="true">
              {ICONS[a.action] ?? '•'}
            </span>
            <div className="timeline__body">
              <p>
                <strong>{a.actor}</strong> {label(a.action)}{' '}
                {a.documentId && nameOf(a.documentId) && (
                  <button className="btn btn--link" onClick={() => onOpen(a.documentId as string)}>
                    {nameOf(a.documentId)}
                  </button>
                )}
              </p>
              {a.detail && a.action !== 'uploaded' && <p className="muted">{a.detail}</p>}
            </div>
            <time className="muted" dateTime={a.at}>
              {formatDateTime(a.at)}
            </time>
          </li>
        ))}
      </ol>
    </Card>
  );
}
