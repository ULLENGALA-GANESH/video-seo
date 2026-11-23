import { useEffect, useState, type FormEvent } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  Eye,
  EyeOff,
  LogOut,
  ScanLine,
  UserRound,
} from 'lucide-react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { errorText, jsonRequest, request, type User } from './api';
import { Brand } from './Shell';
import { safeNext, useSession } from './session';

export function AuthScreen({ signup = false }: { signup?: boolean }) {
  const { user, setUser } = useSession();
  const { search } = useLocation();
  const navigate = useNavigate();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [visible, setVisible] = useState(false);
  const next = safeNext(search);
  const title = signup ? 'Create your account' : 'Welcome back';
  useEffect(() => {
    document.title = title + ' · AI Video SEO Analysis System';
  }, [title]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setError('');
    setBusy(true);
    const values = new FormData(event.currentTarget);
    const body: Record<string, string> = {
      email: String(values.get('email')).trim(),
      password: String(values.get('password')),
    };
    if (signup) body.name = String(values.get('name')).trim();
    try {
      const account = await jsonRequest<User>(
        '/api/auth/' + (signup ? 'signup' : 'login'),
        'POST',
        body,
      );
      setUser(account);
      navigate(next, { replace: true });
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }
  if (user) return <Navigate to={next} replace />;
  return (
    <main className="auth-layout">
      <section className="auth-form-side">
        <div className="auth-brand">
          <Brand />
        </div>
        <div className="auth-form-wrap">
          <Link className="back-link" to="/">
            <ArrowLeft size={15} />
            Back to studio
          </Link>
          <p className="eyebrow">Personal workspace</p>
          <h1>
            {title}
            <span className="heading-dot">.</span>
          </h1>
          <p className="subtle">
            {signup
              ? 'Keep your transcripts, keywords, and reports together in a private studio.'
              : 'Sign in to your studio to create analyses and revisit your reports.'}
          </p>
          <form className="form-stack" onSubmit={submit}>
            {signup && (
              <label className="field">
                <span>Display name</span>
                <input
                  name="name"
                  autoComplete="name"
                  maxLength={80}
                  required
                  placeholder="Your name"
                />
              </label>
            )}
            <label className="field">
              <span>Email address</span>
              <input
                name="email"
                type="email"
                autoComplete="email"
                maxLength={254}
                required
                placeholder="you@example.com"
              />
            </label>
            <div className="field">
              <label htmlFor="account-password">Password</label>
              <span className="password-input">
                <input
                  id="account-password"
                  name="password"
                  type={visible ? 'text' : 'password'}
                  autoComplete={signup ? 'new-password' : 'current-password'}
                  minLength={signup ? 8 : undefined}
                  maxLength={signup ? 128 : 1024}
                  required
                  placeholder={signup ? 'At least 8 characters' : 'Enter your password'}
                />
                <button
                  type="button"
                  className="icon-button"
                  aria-label={visible ? 'Hide password' : 'Show password'}
                  aria-pressed={visible}
                  onClick={() => setVisible(!visible)}
                >
                  {visible ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </span>
            </div>
            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            <button disabled={busy} className="btn btn-primary auth-submit">
              {busy ? 'Please wait…' : signup ? 'Create account' : 'Sign in'}
              <ArrowRight size={17} />
            </button>
          </form>
          <p className="auth-switch">
            {signup ? 'Already have an account?' : 'New here?'}{' '}
            <Link to={(signup ? '/login' : '/signup') + search}>
              {signup ? 'Sign in' : 'Create an account'}
            </Link>
          </p>
        </div>
      </section>
      <aside className="auth-art">
        <img src="/studio-still.jpg" alt="Alpine landscape from the sample report" />
        <div className="auth-art-copy">
          <p className="eyebrow">
            <ScanLine size={16} />
            Featured analysis
          </p>
          <h2>A field guide to alpine photography.</h2>
          <Link to="/analyses/sample" className="text-link">
            Explore the sample report <ArrowUpRight size={16} />
          </Link>
        </div>
      </aside>
    </main>
  );
}

export function Profile() {
  const { user, setUser } = useSession();
  const [name, setName] = useState(user?.name ?? '');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [action, setAction] = useState('');
  if (!user) return null;
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (action) return;
    setError('');
    setMessage('');
    setAction('saving');
    try {
      const updated = await jsonRequest<User>('/api/profile', 'PATCH', { name: name.trim() });
      setUser(updated);
      setName(updated.name);
      setMessage('Your profile has been saved.');
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setAction('');
    }
  }
  async function logout() {
    if (action) return;
    setAction('logout');
    setError('');
    try {
      await request('/api/auth/logout', {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      // Reload the public workspace after the server revokes the session cookie.
      window.location.replace('/');
    } catch (reason) {
      setError(errorText(reason));
      setAction('');
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Account</p>
          <h1>
            Your profile<span className="heading-dot">.</span>
          </h1>
          <p className="subtle">Manage the identity attached to your private workspace.</p>
        </div>
      </div>
      <section className="surface workspace-form profile-card">
        <div className="panel-heading">
          <span className="stat-icon tone-sage">
            <UserRound size={19} />
          </span>
          <h2>Account details</h2>
        </div>
        <form className="form-stack" onSubmit={save}>
          <label className="field">
            <span>Display name</span>
            <input
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                setMessage('');
              }}
              autoComplete="name"
              required
              maxLength={80}
            />
          </label>
          <label className="field">
            <span>Email address</span>
            <input value={user.email} type="email" readOnly />
            <small>Your sign-in email</small>
          </label>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          {message && (
            <p className="form-success" role="status">
              <Check size={16} />
              {message}
            </p>
          )}
          <div className="form-actions">
            <button
              className="btn btn-primary"
              disabled={!!action || !name.trim() || name.trim() === user.name}
            >
              {action === 'saving' ? 'Saving…' : 'Save changes'}
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={!!action}
              onClick={() => void logout()}
            >
              <LogOut size={15} />
              {action === 'logout' ? 'Signing out…' : 'Sign out'}
            </button>
          </div>
        </form>
      </section>
    </>
  );
}
