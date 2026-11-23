import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ApiError, type ApiClient, type ManagedUser, type Role } from '../lib/api';
import type { Client } from '../lib/types';
import { Card, formatDateTime } from './ui';

interface Props {
  api: ApiClient;
  clients: Client[];
  currentUserId: string;
  /** Called after a change, so the activity history picks it up. */
  onChanged: () => void;
  /** A 401 means the session ended; the app handles that. */
  onError: (e: unknown) => void;
}

const ROLE_LABEL: Record<Role, string> = { admin: 'Admin', reviewer: 'Reviewer', client: 'Client' };

function passwordProblem(password: string): string | null {
  if (password.length < 10) return 'Passwords need at least 10 characters.';
  if (password.toLowerCase() === password || password.toUpperCase() === password || !/\d/.test(password)) {
    return 'Use upper and lower case letters and at least one number.';
  }
  return null;
}

export function UsersView({ api, clients, currentUserId, onChanged, onError }: Props) {
  const [users, setUsers] = useState<ManagedUser[] | null>(null);
  const [form, setForm] = useState({ email: '', name: '', role: 'client' as Role, clientId: clients[0]?.id ?? '', password: '' });
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setUsers(await api.listUsers());
    } catch (e) {
      onError(e);
    }
  }, [api, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setDone(null);
    const email = form.email.trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return setProblem('Enter a valid email address.');
    if (form.role === 'client' && !form.clientId) return setProblem('Choose which client this person belongs to.');
    const weak = passwordProblem(form.password);
    if (weak) return setProblem(weak);
    setBusy(true);
    setProblem(null);
    try {
      const created = await api.createUser({ ...form, email, name: form.name.trim() });
      setDone(`Created ${created.email}. Share the password with them securely (not by email).`);
      setForm((f) => ({ ...f, email: '', name: '', password: '' }));
      await load();
      onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.status !== 401) setProblem(err.message);
      else onError(err);
    } finally {
      setBusy(false);
    }
  };

  const disable = async (user: ManagedUser) => {
    if (!window.confirm(`Disable ${user.email}? They will be signed out and can no longer sign in.`)) return;
    try {
      await api.disableUser(user.id);
      await load();
      onChanged();
    } catch (e) {
      onError(e);
    }
  };

  const clientName = (id: string | null) => clients.find((c) => c.id === id)?.name ?? '—';

  return (
    <div className="stack">
      <Card title="Add a user">
        <form className="user-form" onSubmit={(e) => void create(e)} noValidate>
          <label>
            <span>Email</span>
            <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} autoComplete="off" />
          </label>
          <label>
            <span>Name</span>
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoComplete="off" />
          </label>
          <label>
            <span>Role</span>
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
              <option value="client">Client (one business)</option>
              <option value="reviewer">Reviewer (all clients)</option>
              <option value="admin">Admin (full access)</option>
            </select>
          </label>
          {form.role === 'client' && (
            <label>
              <span>Client</span>
              <select value={form.clientId} onChange={(e) => setForm({ ...form, clientId: e.target.value })}>
                {clients.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            <span>Temporary password</span>
            <input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" />
          </label>
          <div className="user-form__actions">
            <button className="btn btn--primary" type="submit" disabled={busy}>
              {busy ? 'Creating…' : 'Create user'}
            </button>
          </div>
          {problem && (
            <div className="notice notice--error user-form__wide" role="alert">
              {problem}
            </div>
          )}
          {done && (
            <div className="notice user-form__wide" role="status">
              {done}
            </div>
          )}
        </form>
      </Card>

      <Card title="Users" actions={<span className="muted">{users ? `${users.length} people` : ''}</span>}>
        {users === null ? (
          <p className="empty">Loading users…</p>
        ) : (
          <div className="table-wrap">
            <table className="table users-table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Name</th>
                  <th>Role</th>
                  <th>Client</th>
                  <th>Added</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} className={u.disabled ? 'is-disabled' : ''}>
                    <td>{u.email}</td>
                    <td>{u.name}</td>
                    <td>
                      <span className={`role-badge role-badge--${u.role}`}>{ROLE_LABEL[u.role]}</span>
                    </td>
                    <td>{u.role === 'client' ? clientName(u.clientId) : '—'}</td>
                    <td className="muted">{u.createdAt ? formatDateTime(u.createdAt) : ''}</td>
                    <td className="users-table__action">
                      {u.disabled ? (
                        <span className="muted">Disabled</span>
                      ) : u.id === currentUserId ? (
                        <span className="muted">You</span>
                      ) : (
                        <button className="btn btn--link btn--danger-link" onClick={() => void disable(u)}>
                          Disable
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
