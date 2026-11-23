import { useState, type FormEvent } from 'react';

interface Props {
  apiUrl: string;
  /** Sample accounts offered by a demo API (never shown for real deployments). */
  demoAccounts?: { password: string; emails: string[] };
  notice: string | null;
  onSignIn: (email: string, password: string) => Promise<string | null>;
  onUseDemo: () => void;
}

const ROLE_HINTS: Record<string, string> = {
  admin: 'Admin: everything, plus reminders and audit export',
  ana: 'Reviewer: review and approve documents',
  accounts: 'Client: Harbour & Finch portal',
  hello: 'Client: Fernhill portal',
};

function hintFor(email: string): string {
  const key = Object.keys(ROLE_HINTS).find((k) => email.startsWith(k));
  return key ? ROLE_HINTS[key] : email;
}

export function SignIn({ apiUrl, demoAccounts, notice, onSignIn, onUseDemo }: Props) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e?: FormEvent, override?: { email: string; password: string }) => {
    e?.preventDefault();
    const creds = override ?? { email, password };
    if (!creds.email.trim() || !creds.password) {
      setProblem('Enter your email and password.');
      return;
    }
    setBusy(true);
    setProblem(null);
    const failure = await onSignIn(creds.email.trim(), creds.password);
    setBusy(false);
    if (failure) {
      setProblem(failure);
      setPassword('');
    }
  };

  return (
    <div className="signin">
      <form className="card signin__card" onSubmit={(e) => void submit(e)} noValidate>
        <h1 className="signin__title">Sign in</h1>
        <p className="muted signin__sub">to the document portal at {apiUrl}</p>
        {notice && (
          <div className="notice" role="status">
            {notice}
          </div>
        )}
        <label className="signin__field">
          <span>Email</span>
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </label>
        <label className="signin__field">
          <span>Password</span>
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {problem && (
          <div className="notice notice--error" role="alert">
            {problem}
          </div>
        )}
        <button className="btn btn--primary signin__submit" type="submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>

        {demoAccounts && demoAccounts.emails.length > 0 && (
          <div className="signin__demo">
            <p className="muted">Demo server: try a role with one click.</p>
            <div className="signin__accounts">
              {demoAccounts.emails.map((demoEmail) => (
                <button
                  key={demoEmail}
                  type="button"
                  className="btn btn--ghost btn--small"
                  disabled={busy}
                  onClick={() => {
                    setEmail(demoEmail);
                    void submit(undefined, { email: demoEmail, password: demoAccounts.password });
                  }}
                >
                  {hintFor(demoEmail)}
                </button>
              ))}
            </div>
          </div>
        )}
        <button type="button" className="btn btn--link signin__back" onClick={onUseDemo}>
          Use the browser demo instead
        </button>
      </form>
    </div>
  );
}
