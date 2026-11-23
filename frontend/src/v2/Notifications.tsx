import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Bell, Check } from 'lucide-react';
import { request, errorText } from './api';

interface Notice {
  id: string;
  title: string;
  message: string;
  created_at: string;
  read: boolean;
  report_id?: string;
}

export default function Notifications() {
  const [items, setItems] = useState<Notice[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    request<Notice[]>('/api/notifications', { signal: controller.signal })
      .then((rows) => {
        if (!controller.signal.aborted) setItems(rows);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(errorText(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [version]);
  async function mark(id: string) {
    try {
      await request('/api/notifications/' + id + '/read', {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      setItems((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)));
    } catch (e) {
      setError(errorText(e));
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Notifications</h1>
          <p className="subtle">Recent analysis and YouTube channel activity.</p>
        </div>
        <button
          className="btn btn-secondary"
          disabled={loading}
          onClick={() => setVersion((v) => v + 1)}
        >
          Refresh
        </button>
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <section className="analysis-list">
        {loading ? (
          <p className="loading-state">Loading activity…</p>
        ) : items.length ? (
          items.map((n) => (
            <article className="analysis-row" key={n.id}>
              <Bell size={18} />
              <div className="row-main">
                <strong>{n.title}</strong>
                <p>{n.message}</p>
                <small>{new Date(n.created_at).toLocaleString()}</small>
                {n.report_id && (
                  <Link className="text-link" to={'/analyses/' + n.report_id}>
                    Open report
                  </Link>
                )}
              </div>
              {n.read ? (
                <Check aria-label="Read" size={16} />
              ) : (
                <button className="btn btn-ghost" onClick={() => void mark(n.id)}>
                  Mark read
                </button>
              )}
            </article>
          ))
        ) : (
          !error && (
            <div className="empty-state">
              <Bell />
              <h2>No activity yet</h2>
              <p>Your next completed analysis will appear here.</p>
            </div>
          )
        )}
      </section>
    </>
  );
}
