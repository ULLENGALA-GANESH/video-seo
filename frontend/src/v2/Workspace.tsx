import { useState } from 'react';
import {
  ArrowRight,
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  FileText,
  Film,
  FolderOpen,
  Hash,
  Layers3,
  Plus,
  ScanLine,
  Search,
  Tag,
  Type,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { reportDate, reportHref, type Status, type Summary } from './api';
import { useHistory } from './data';
import { useSession } from './session';

const statusNames: Record<Status, string> = {
  completed: 'Completed',
  queued: 'Queued',
  processing: 'Processing',
  failed: 'Failed',
};
export function StatusBadge({ status }: { status: Status }) {
  const Icon = status === 'completed' ? CheckCircle2 : status === 'failed' ? CircleAlert : Clock3;
  return (
    <span className={'status status-' + status}>
      <Icon size={12} />
      {statusNames[status]}
    </span>
  );
}

export function NewButton() {
  return (
    <Link to="/new" className="btn btn-primary">
      <Plus size={17} />
      New analysis
    </Link>
  );
}

function AnalysisRow({ item, demo }: { item: Summary; demo: boolean }) {
  return (
    <Link to={reportHref(item, demo)} className="analysis-row">
      <div className={'thumb' + (demo ? ' thumb-photo' : '')}>
        {demo ? (
          <img src="/studio-still.jpg" alt="" loading="lazy" />
        ) : item.source_type === 'video' ? (
          <Film size={23} />
        ) : (
          <FileText size={23} />
        )}
      </div>
      <div className="row-main">
        <strong>{item.title}</strong>
        <span className="row-meta">
          {demo
            ? 'Sample report'
            : item.source_type === 'video'
              ? 'Media upload'
              : 'Pasted transcript'}{' '}
          <span aria-hidden="true">·</span> {reportDate(item.created_at)}
        </span>
      </div>
      <span className="row-count">
        <Tag size={14} />
        {item.keyword_count ?? 0} keywords
      </span>
      <StatusBadge status={item.status} />
      <ChevronRight className="row-arrow" size={17} />
    </Link>
  );
}

function ListState({
  loading,
  error,
  retry,
  empty,
}: {
  loading: boolean;
  error: string;
  retry: () => void;
  empty: boolean;
}) {
  if (error)
    return (
      <div className="empty-state" role="alert">
        <p>{error}</p>
        <button className="btn btn-secondary" onClick={retry}>
          Try again
        </button>
      </div>
    );
  if (loading)
    return (
      <p className="loading-state" role="status">
        Loading analyses…
      </p>
    );
  if (empty)
    return (
      <div className="empty-state">
        <FolderOpen size={26} />
        <h3>Your first report starts here</h3>
        <p>Analyze a video or paste a transcript to find keywords in your content.</p>
        <NewButton />
      </div>
    );
  return null;
}

export function Overview() {
  const { user } = useSession();
  const { items, cursor, loading, error, reload } = useHistory(user?.id);
  const newest = items.find((item) => item.status === 'completed');
  let reportNote = 'Latest completed report';
  if (!loading && !error && !newest) {
    reportNote = cursor ? 'No completed report on this page' : 'No completed report yet';
  }
  const metrics = [
    {
      label: 'Analyses',
      value: loading || error ? '—' : String(items.length) + (cursor ? '+' : ''),
      note: 'In this workspace',
      Icon: Layers3,
      color: 'sage',
    },
    {
      label: 'Keywords found',
      value: newest?.keyword_count.toLocaleString() ?? '—',
      note: reportNote,
      Icon: Hash,
      color: 'peach',
    },
    {
      label: 'Words analyzed',
      value: newest?.word_count.toLocaleString() ?? '—',
      note: reportNote,
      Icon: Type,
      color: 'lilac',
    },
  ];
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            <span className="eyebrow-rule" />
            Studio dashboard
          </p>
          <h1>
            Workspace overview<span className="heading-dot">.</span>
          </h1>
          <p className="subtle">
            {user
              ? 'Review your keywords, YouTube research, and publishing drafts.'
              : 'Find transcript keywords, research them on YouTube, and prepare publishing metadata.'}
          </p>
        </div>
        <NewButton />
      </div>
      <section className="feature-hero" aria-labelledby="featured-title">
        <div className="hero-copy">
          <p className="eyebrow">
            <span className="hero-label-mark" />
            <span>Featured analysis</span>
          </p>
          <h2 id="featured-title">
            A field guide to alpine photography<span className="hero-period">.</span>
          </h2>
          <p className="hero-meta">
            See how a field transcript becomes a keyword report, one phrase at a time.
          </p>
          <Link className="btn btn-lime" to="/analyses/sample">
            Explore sample report <ArrowUpRight size={16} />
          </Link>
        </div>
        <Link
          to="/analyses/sample"
          className="hero-image"
          aria-label="View the alpine photography sample report"
        >
          <img
            src="/studio-still.jpg"
            alt="Alpine landscape from the featured photography report"
            fetchPriority="high"
          />
          <span className="image-caption">
            <ScanLine size={14} />
            Narrated sample
          </span>
        </Link>
      </section>
      <div className="stats-grid">
        {metrics.map(({ label, value, note, Icon, color }) => (
          <div className={'stat-card stat-' + color} key={label}>
            <div className="stat-head">
              <span className={'stat-icon tone-' + color}>
                <Icon size={18} />
              </span>
              <span className="stat-label">{label}</span>
            </div>
            <strong className="stat-value">{value}</strong>
            <span className="stat-note">{note}</span>
          </div>
        ))}
      </div>
      <section className="recent-section">
        <div className="section-heading">
          <div>
            <h2>Recent analyses</h2>
          </div>
          <Link to="/analyses" className="text-link">
            View library <ArrowRight size={15} />
          </Link>
        </div>
        <div className="analysis-list">
          <ListState loading={loading} error={error} retry={reload} empty={items.length === 0} />
          {items.slice(0, 3).map((item) => (
            <AnalysisRow key={item.id} item={item} demo={!user} />
          ))}
        </div>
      </section>
    </>
  );
}

export function Library() {
  const { user } = useSession();
  const { items, cursor, loading, error, reload, loadMore, morePending } = useHistory(user?.id);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const query = search.trim().toLowerCase();
  const visible = items.filter(
    (item) =>
      (status === 'all' || item.status === status) &&
      item.title.toLowerCase().includes(query),
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Workspace</p>
          <h1>
            Analysis library<span className="heading-dot">.</span>
          </h1>
          <p className="subtle">
            {user
              ? 'Revisit your reports and follow work in progress.'
              : 'Browse the sample report and its supporting transcript.'}
          </p>
        </div>
        <NewButton />
      </div>
      <div className="library-toolbar">
        <label className="search-field">
          <Search size={17} />
          <input
            type="search"
            aria-label="Filter loaded analyses by title"
            placeholder="Filter by title"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <label className="filter-field">
          <span>Status</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="all">All statuses</option>
            {Object.entries(statusNames).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="analysis-list">
        <ListState loading={loading} error={error} retry={reload} empty={!items.length} />
        {visible.map((item) => (
          <AnalysisRow key={item.id} item={item} demo={!user} />
        ))}
        {!loading && !error && items.length > 0 && !visible.length && (
          <div className="empty-state">
            <p>No loaded reports match these filters.</p>
            <button
              className="btn btn-secondary"
              onClick={() => {
                setSearch('');
                setStatus('all');
              }}
            >
              Clear filters
            </button>
          </div>
        )}
      </div>
      {cursor && (
        <button
          className="btn btn-secondary load-more"
          onClick={() => void loadMore()}
          disabled={morePending}
        >
          {morePending ? 'Loading…' : 'Load more analyses'}
        </button>
      )}
    </>
  );
}
