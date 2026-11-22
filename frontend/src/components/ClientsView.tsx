import { checklist, reminderPreviews, type DemoState } from '../lib/demoStore';
import { Card, ChecklistChip } from './ui';

export function ClientsView({ state }: { state: DemoState }) {
  const reminders = reminderPreviews(state);
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
        actions={<span className="pill">Preview · sending arrives in milestone 3</span>}
      >
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
