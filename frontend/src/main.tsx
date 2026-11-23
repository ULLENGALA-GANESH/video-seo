import { lazy, StrictMode, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Link, Navigate, Route, Routes, useParams } from 'react-router-dom';
import { Shell } from './v2/Shell';
import { Library, Overview } from './v2/Workspace';
import { Private, SessionProvider } from './v2/session';
import './v2/styles.css';

const AuthScreen = lazy(() =>
  import('./v2/Account').then((module) => ({ default: module.AuthScreen })),
);
const Profile = lazy(() => import('./v2/Account').then((module) => ({ default: module.Profile })));
const ReportPage = lazy(() => import('./v2/Report'));
const NewAnalysis = lazy(() => import('./v2/NewAnalysis'));

const Privacy = lazy(() => import('./v2/Privacy'));
const Channel = lazy(() => import('./v2/Channel'));
const Notifications = lazy(() => import('./v2/Notifications'));

function LegacyReport() {
  const { id } = useParams();
  return <Navigate to={'/analyses/' + encodeURIComponent(id ?? '')} replace />;
}

function NotFound() {
  return (
    <section className="empty-state">
      <p className="eyebrow">404</p>
      <h1>Page not found</h1>
      <p>Return to your studio to find an analysis.</p>
      <Link to="/" className="btn btn-primary">
        Workspace overview
      </Link>
    </section>
  );
}
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <Suspense
          fallback={
            <p className="loading-state" role="status">
              Opening studio…
            </p>
          }
        >
          <Routes>
            <Route path="/login" element={<AuthScreen key="login" />} />
            <Route path="/signup" element={<AuthScreen key="signup" signup />} />
            <Route element={<Shell />}>
              <Route index element={<Overview />} />
              <Route path="dashboard" element={<Navigate to="/" replace />} />
              <Route path="results/:id" element={<LegacyReport />} />
              <Route path="analyses" element={<Library />} />
              <Route path="analyses/sample" element={<ReportPage key="sample" sample />} />
              <Route
                path="analyses/:id"
                element={
                  <Private>
                    <ReportPage key="private" />
                  </Private>
                }
              />
              <Route
                path="new"
                element={
                  <Private>
                    <NewAnalysis />
                  </Private>
                }
              />
              <Route
                path="profile"
                element={
                  <Private>
                    <Profile />
                  </Private>
                }
              />
              <Route path="privacy" element={<Privacy />} />
              <Route
                path="channel"
                element={
                  <Private>
                    <Channel />
                  </Private>
                }
              />
              <Route
                path="notifications"
                element={
                  <Private>
                    <Notifications />
                  </Private>
                }
              />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </Suspense>
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
);
