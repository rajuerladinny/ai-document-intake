import { useState } from 'react';
import type { ReminderResult } from '../lib/api';
import { checklist, reminderPreviews, type DemoState } from '../lib/demoStore';
import { Card, ChecklistChip } from './ui';

const STATUS_TEXT: Record<ReminderResult['status'], string> = {
  sent: 'Sent',
  skipped: 'Skipped',
  failed: 'Failed',
  would_send: 'Would send',
};

interface Props {
  state: DemoState;
  /** Admins in API mode can send the reminders for real. */
  onSendReminders?: () => Promise<ReminderResult[] | null>;
}

export function ClientsView({ state, onSendReminders }: Props) {
  const reminders = reminderPreviews(state);
  const [sending, setSending] = useState(false);
  const [results, setResults] = useState<ReminderResult[] | null>(null);

  const send = async () => {
    if (!onSendReminders) return;
    setSending(true);
    setResults(await onSendReminders());
    setSending(false);
  };
  return (
    <div className="stack">
      <div className="client-grid">
        {state.clients.map((client) => {
          const items = checklist(state, client.id);
          const docs = state.documents.filter((d) => d.clientId === client.id);
          const done = items.filter((i) => i.state === 'approved').length;
          return (
            <Card key={client.id} title={client.name} actions={<span className="muted">{client.contactEmail}</span>}>
              <div className="progress" aria-label={`${done} of ${items.length} approved`}>
                <span className="progress__fill" style={{ width: `${items.length ? (done / items.length) * 100 : 0}%` }} />
              </div>
              <p className="muted">
                {done} of {items.length} required documents approved · {docs.length} uploaded
              </p>
              <ul className="checklist">
                {items.map((item) => (
                  <li key={item.docType}>
                    <ChecklistChip type={item.docType} state={item.state} />
                  </li>
                ))}
              </ul>
            </Card>
          );
        })}
      </div>

      <Card
        title="Reminder emails"
        actions={
          onSendReminders ? (
            <button className="btn btn--primary btn--small" onClick={() => void send()} disabled={sending || reminders.length === 0}>
              {sending ? 'Sending…' : `Send ${reminders.length} reminder${reminders.length === 1 ? '' : 's'}`}
            </button>
          ) : (
            <span className="pill">Preview</span>
          )
        }
      >
        {results && (
          <div className="notice" role="status">
            <ul className="reminder-results">
              {results.map((r) => (
                <li key={r.clientId}>
                  <strong>{STATUS_TEXT[r.status]}</strong> · {r.to}
                  {r.reason && <span className="muted"> · {r.reason}</span>}
                </li>
              ))}
            </ul>
            {results.length === 0 && 'Nobody needed a reminder.'}
          </div>
        )}
        {reminders.length === 0 ? (
          <p className="empty">Every client has sent everything. No reminders needed.</p>
        ) : (
          <ul className="emails">
            {reminders.map((r) => (
              <li key={r.clientId} className="email">
                <p className="email__meta">
                  <strong>To:</strong> {r.to}
                </p>
                <p className="email__meta">
                  <strong>Subject:</strong> {r.subject}
                </p>
                <p className="email__body">{r.body}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
