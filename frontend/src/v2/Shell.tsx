import { useEffect, useState } from 'react';
import {
  BookOpen,
  Bell,
  Tv,
  ChevronRight,
  Clapperboard,
  FolderOpen,
  LayoutDashboard,
  LogIn,
  Menu,
  Plus,
  ScanLine,
  UserRound,
  X,
} from 'lucide-react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useSession } from './session';

export function Brand() {
  return (
    <Link to="/" className="brand" aria-label="AI Video SEO Analysis System home">
      <span className="brand-mark" aria-hidden="true">
        <ScanLine size={25} strokeWidth={2.25} />
      </span>
      <span>
        AI Video SEO <small>Analysis System</small>
      </span>
    </Link>
  );
}

const pageNames: Record<string, string> = {
  '/': 'Overview',
  '/analyses': 'Analysis library',
  '/new': 'New analysis',
  '/profile': 'Profile',
  '/privacy': 'Data and privacy',
  '/channel': 'YouTube channel',
  '/notifications': 'Notifications',
};

export function Shell() {
  const { user } = useSession();
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 780px)').matches);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 780px)');
    const resize = () => {
      setMobile(media.matches);
      if (!media.matches) setMenuOpen(false);
    };
    media.addEventListener('change', resize);
    return () => media.removeEventListener('change', resize);
  }, []);
  const current =
    pageNames[pathname] || (pathname.startsWith('/analyses/') ? 'Analysis report' : 'Page');
  useEffect(() => {
    setMenuOpen(false);
    document.title = current + ' · AI Video SEO Analysis System';
    window.scrollTo(0, 0);
  }, [pathname, current]);
  useEffect(() => {
    if (!menuOpen) return;
    document.querySelector<HTMLButtonElement>('.sidebar-close')?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false);
      } else if (event.key === 'Tab') {
        const links = document.querySelectorAll<HTMLElement>(
          '#studio-navigation a, #studio-navigation button',
        );
        const first = links[0];
        const last = links[links.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        }
        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('keydown', close);
      document.body.style.overflow = previousOverflow;
      document.querySelector<HTMLButtonElement>('.mobile-menu-toggle')?.focus();
    };
  }, [menuOpen]);
  const initials = user
    ? user.name
        .split(/\s+/)
        .map((word) => word[0])
        .slice(0, 2)
        .join('')
        .toUpperCase()
    : 'G';
  const navClass = ({ isActive }: { isActive: boolean }) =>
    'nav-link' + (isActive ? ' active' : '');
  return (
    <div className={'app-shell' + (menuOpen ? ' menu-open' : '')}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      {menuOpen && (
        <button
          className="nav-scrim"
          onClick={() => setMenuOpen(false)}
          aria-label="Close navigation"
        />
      )}
      <aside
        className={'sidebar' + (menuOpen ? ' open' : '')}
        id="studio-navigation"
        inert={mobile && !menuOpen}
      >
        <div className="sidebar-header">
          <Brand />
          <button
            className="sidebar-close icon-button"
            onClick={() => setMenuOpen(false)}
            aria-label="Close navigation"
          >
            <X size={20} />
          </button>
        </div>
        <div className="workspace-switch">
          <span className="workspace-icon">
            <Clapperboard size={19} />
          </span>
          <div>
            <strong>{user ? 'Personal studio' : 'Demo studio'}</strong>
            <small>{user ? 'Private workspace' : 'Sample workspace'}</small>
          </div>
        </div>
        <nav aria-label="Studio navigation" onClick={() => setMenuOpen(false)}>
          <div className="nav-group">
            <p className="nav-label">Workspace</p>
            <NavLink to="/" end className={navClass}>
              <LayoutDashboard />
              Overview
            </NavLink>
            <NavLink to="/analyses" end className={navClass}>
              <FolderOpen />
              Analysis library
            </NavLink>
            <NavLink to="/new" className={navClass}>
              <Plus />
              New analysis
            </NavLink>
          </div>
          <div className="nav-group">
            <p className="nav-label">Explore</p>
            <NavLink to="/analyses/sample" className={navClass}>
              <BookOpen />
              Sample report
            </NavLink>
            <NavLink to="/channel" className={navClass}>
              <Tv />
              YouTube channel
            </NavLink>
            <NavLink to="/notifications" className={navClass}>
              <Bell />
              Notifications
            </NavLink>
            <NavLink to="/profile" className={navClass}>
              <UserRound />
              Profile
            </NavLink>
          </div>
        </nav>
        <div className="sidebar-bottom">
          <Link className="account-link" to={user ? '/profile' : '/login'}>
            <span className="avatar">{user ? initials : <UserRound size={19} />}</span>
            <span>
              <strong>{user?.name || 'Guest workspace'}</strong>
              <small>{user ? 'Manage profile' : 'Sign in to save work'}</small>
            </span>
            <ChevronRight size={15} />
          </Link>
        </div>
      </aside>
      <div className="main-column" inert={mobile && menuOpen}>
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="mobile-menu-toggle icon-button"
              aria-label="Open navigation"
              aria-expanded={menuOpen}
              aria-controls="studio-navigation"
              onClick={() => setMenuOpen(true)}
            >
              <Menu size={20} />
            </button>
            <span>Workspace</span>
            <ChevronRight size={14} />
            <strong>{current}</strong>
          </div>
          <div className="topbar-actions">
            <span className="workspace-badge">
              <i aria-hidden="true" />
              {user ? 'Private workspace' : 'Demo workspace'}
            </span>
            {user ? (
              <Link to="/profile" className="top-avatar avatar" aria-label="Open profile">
                {initials}
              </Link>
            ) : (
              <Link className="top-login" to="/login">
                Sign in <LogIn size={15} />
              </Link>
            )}
          </div>
        </header>
        <main id="main" className="content" tabIndex={-1}>
          <Outlet key={user?.id ?? 'guest'} />
        </main>
      </div>
    </div>
  );
}
