import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { request, type User } from './api';

interface Session {
  user: User | null;
  pending: boolean;
  setUser: (user: User | null) => void;
}
const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setAccount] = useState<User | null>(null);
  const [pending, setPending] = useState(true);
  const bootstrap = useRef<AbortController | null>(null);
  const setUser = useCallback((account: User | null) => {
    bootstrap.current?.abort();
    setAccount(account);
    setPending(false);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    bootstrap.current = controller;
    const expire = () => setUser(null);
    window.addEventListener('framewise-session-expired', expire);
    request<User>('/api/auth/me', { signal: controller.signal })
      .then((account) => {
        if (!controller.signal.aborted) setAccount(account);
      })
      .catch(() => {})
      .finally(() => {
        if (!controller.signal.aborted) setPending(false);
      });
    return () => {
      controller.abort();
      window.removeEventListener('framewise-session-expired', expire);
    };
  }, [setUser]);
  return (
    <SessionContext.Provider value={{ user, pending, setUser }}>{children}</SessionContext.Provider>
  );
}

export function useSession() {
  const session = useContext(SessionContext);
  if (!session) throw new Error('Session provider is missing.');
  return session;
}

export function Private({ children }: { children: ReactNode }) {
  const { pending, user } = useSession();
  const location = useLocation();
  if (pending)
    return (
      <p className="loading-state" role="status">
        Opening your workspace…
      </p>
    );
  if (!user)
    return (
      <Navigate
        to={'/login?next=' + encodeURIComponent(location.pathname + location.search)}
        replace
      />
    );
  return children;
}

export function safeNext(search: string) {
  const path = new URLSearchParams(search).get('next') || '/';
  return /^\/(?:new|profile|notifications|channel(?:\?report=[a-f0-9]{24})?|analyses(?:\/[a-zA-Z0-9_-]+)?)?$/.test(
    path,
  )
    ? path
    : '/';
}
