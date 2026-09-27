import { useEffect, useState, type FormEvent } from 'react';
import { api } from '../lib/api';
import { useAuth } from '../lib/store';

interface PublicSettings {
  demo_admin_login: boolean;
}

export default function LoginView() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [demoToken, setDemoToken] = useState<string | null>(null);
  // An admin can hide the demo shortcuts; assume visible until told otherwise.
  const [demoAdminLogin, setDemoAdminLogin] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void api
      .get<PublicSettings>('/auth/public-settings')
      .then((settings) => {
        if (!cancelled) setDemoAdminLogin(settings.demo_admin_login !== false);
      })
      // A failure here must never block signing in.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await (mode === 'login' ? login(email.trim(), password) : register(email.trim(), password, displayName));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setBusy(false);
    }
  };

  const tryDemo = async (which: 'admin' | 'user') => {
    setDemoToken(which);
    setError(null);
    setBusy(true);
    try {
      const email = which === 'admin' ? 'admin@myaibuddy.dev' : 'callie@myaibuddy.dev';
      const password = which === 'admin' ? 'DevPass1234' : 'CalliePass1';
      await login(email, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Demo login failed');
    } finally {
      setDemoToken(null);
      setBusy(false);
    }
  };

  return (
    <div className="grid h-full place-items-center">
      <div className="card mab-card w-full max-w-md p-8">
        <div className="mb-6 text-center">
          <div className="mb-2 text-4xl">🤖</div>
          <h1 className="mab-heading text-xl">MyAIBuddy</h1>
          <p className="mab-subtle mt-1 text-sm">
            {mode === 'login' ? 'Welcome back' : 'Create your workspace account'}
          </p>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <div className="mab-field">
            <label htmlFor="email" className="mab-label">
              Email
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mab-input"
              placeholder="you@example.com"
            />
          </div>

          {mode === 'register' && (
            <div className="mab-field">
              <label htmlFor="displayName" className="mab-label">
                Display name
              </label>
              <input
                id="displayName"
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="mab-input"
                placeholder="Optional"
              />
            </div>
          )}

          <div className="mab-field">
            <label htmlFor="password" className="mab-label">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mab-input"
              placeholder="••••••••"
            />
            {mode === 'register' && (
              <p className="mab-hint">At least 8 characters, one uppercase letter and one digit.</p>
            )}
          </div>

          {error && (
            <p className="mab-error" role="alert">
              {error}
            </p>
          )}

          <div className="flex items-center justify-between gap-3 pt-1">
            <button
              type="button"
              className="mab-btn mab-btn-ghost mab-btn-md"
              onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
            >
              {mode === 'login' ? 'Create an account' : 'I have an account'}
            </button>
            <button type="submit" className="mab-btn mab-btn-primary mab-btn-md" disabled={busy}>
              {busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Register'}
            </button>
          </div>
        </form>

        <div className="mt-6 border-t border-mab-border pt-4">
          <p className="mab-subtle mb-2 text-xs">Try it instantly:</p>
          <div className="flex gap-2">
            {demoAdminLogin && (
              <button
                type="button"
                className="mab-btn mab-btn-secondary mab-btn-sm flex-1"
                disabled={Boolean(demoToken)}
                onClick={() => tryDemo('admin')}
              >
                {demoToken === 'admin' ? 'Signing in…' : 'Admin demo'}
              </button>
            )}
            <button
              type="button"
              className="mab-btn mab-btn-secondary mab-btn-sm flex-1"
              disabled={Boolean(demoToken)}
              onClick={() => tryDemo('user')}
            >
              {demoToken === 'user' ? 'Signing in…' : 'User demo'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}