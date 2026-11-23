import { useEffect, useState, type FormEvent } from 'react';
import { BarChart3, Film, Link2, UploadCloud } from 'lucide-react';
import { Link, useSearchParams } from 'react-router-dom';
import { errorText, jsonRequest, request } from './api';
import { useSession } from './session';
import {
  DESCRIPTION_BYTE_LIMIT, SavedChoices, descriptionBytes, type Copy, type Draft,
} from './PublishingDraft';
import './channel.css';

const base = '/api/channel';
type ChannelInfo = {
  id: string;
  title: string;
  views: number | null;
  subscribers: number | null;
  videos: number | null;
};
type Connection = { configured: boolean; channel: ChannelInfo | null };
type Video = {
  id: string;
  title: string;
  description: string;
  tags: string[];
  etag: string;
  visibility: string | null;
};
type UploadResult = {
  id: string;
  offset: number;
  size: number;
  video_id: string | null;
  visibility: string | null;
};
type SavedUpload = {
  id: string;
  hash: string;
  title: string;
  visibility: string;
};
type Analytics = {
  channel: ChannelInfo;
  start: string;
  end: string;
  rows: {
    day: string;
    views: number;
    estimatedMinutesWatched: number;
    subscribersGained: number;
    subscribersLost: number;
  }[];
};
const emptyCopy: Copy = { title: '', description: '', tags: '' };
const chunkSize = 4 * 1024 * 1024;
const validTypes: Record<string, string> = {
  mp4: 'video/mp4',
  m4v: 'video/mp4',
  mov: 'video/quicktime',
  webm: 'video/webm',
  mkv: 'video/x-matroska',
};
const oauthMessages: Record<string, string> = {
  success: 'Your YouTube channel is connected.',
  declined: 'You declined the connection.',
  expired: 'The connection request expired. Start a new connection.',
  permissions: 'Google did not grant the required permissions.',
  channel: 'No eligible YouTube channel was found.',
  quota: 'Google reached a quota limit. Try again later.',
  failed: 'Google could not complete the connection. Try again.',
};

function values(copy: Copy) {
  return {
    title: copy.title.trim(),
    description: copy.description.trim(),
    tags: copy.tags
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean),
  };
}

function CopyFields({
  copy,
  change,
}: {
  copy: Copy;
  change: (copy: Copy) => void;
}) {
  return (
    <>
      <label className="field">
        <span>Video title</span>
        <input
          required
          maxLength={100}
          value={copy.title}
          onChange={(e) => change({ ...copy, title: e.target.value })}
        />
      </label>
      <label className="field">
        <span>Description</span>
        <textarea
          rows={4}
          maxLength={DESCRIPTION_BYTE_LIMIT}
          value={copy.description}
          onChange={(e) => change({ ...copy, description: e.target.value })}
        />
        <small>{descriptionBytes(copy.description).toLocaleString()} / 5,000 bytes</small>
      </label>
      <label className="field">
        <span>Tags, separated by commas</span>
        <input
          maxLength={500}
          value={copy.tags}
          onChange={(e) => change({ ...copy, tags: e.target.value })}
        />
      </label>
    </>
  );
}

function AnalyticsPanel({ channel }: { channel: ChannelInfo }) {
  const [data, setData] = useState<Analytics | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  async function refresh() {
    setBusy(true);
    setError('');
    try {
      setData(await request<Analytics>(base + '/analytics'));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const stats = data?.channel || channel;
  return (
    <section className="channel-panel">
      <div className="channel-heading">
        <h2>Channel analytics</h2>
        <BarChart3 aria-hidden="true" />
      </div>
      <dl className="channel-stats">
        {[
          ['Lifetime views', stats.views],
          ['Subscribers', stats.subscribers],
          ['Videos', stats.videos],
        ].map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>
              {typeof v === 'number' ? v.toLocaleString() : 'Unavailable'}
            </dd>
          </div>
        ))}
      </dl>
      <button className="btn btn-secondary" disabled={busy} onClick={refresh}>
        {busy
          ? 'Loading analytics…'
          : data
            ? 'Refresh analytics'
            : 'Load 28-day analytics'}
      </button>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {data && (
        <>
          <p className="subtle">
            {data.start} – {data.end} · Source: YouTube Analytics
          </p>
          {data.rows.length === 0 ? (
            <p>YouTube returned no activity for this period.</p>
          ) : (
            <div className="channel-table">
              <table>
                <caption>Daily channel activity</caption>
                <thead>
                  <tr>
                    <th>Day</th>
                    <th>Views</th>
                    <th>Watch minutes</th>
                    <th>Subscribers gained</th>
                    <th>Subscribers lost</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row) => (
                    <tr key={row.day}>
                      <td>{row.day}</td>
                      <td>{row.views}</td>
                      <td>{row.estimatedMinutesWatched}</td>
                      <td>{row.subscribersGained}</td>
                      <td>{row.subscribersLost}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function VideoLibrary() {
  const [items, setItems] = useState<Video[]>([]),
    [next, setNext] = useState<string | null>(''),
    [loaded, setLoaded] = useState(false);
  const [edit, setEdit] = useState<Video | null>(null),
    [copy, setCopy] = useState<Copy>(emptyCopy),
    [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState('');
  async function load(append: boolean) {
    setBusy(true);
    setError('');
    try {
      const result = await request<{ items: Video[]; next: string | null }>(
        base +
          '/videos' +
          (append && next ? '?page=' + encodeURIComponent(next) : ''),
      );
      setItems((old) => (append ? [...old, ...result.items] : result.items));
      setNext(result.next);
      setLoaded(true);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!edit || !confirm) return;
    setBusy(true);
    setError('');
    try {
      const item = await jsonRequest<Video>(
        base + '/videos/' + encodeURIComponent(edit.id),
        'PATCH',
        { ...values(copy), etag: edit.etag, confirmed: true },
      );
      setItems((old) => old.map((v) => (v.id === item.id ? item : v)));
      setEdit(null);
      setMessage('Metadata saved on YouTube.');
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="channel-panel">
      <div className="channel-heading">
        <h2>Video library</h2>
        <Film aria-hidden="true" />
      </div>
      <button
        className="btn btn-secondary"
        disabled={busy}
        onClick={() => load(false)}
      >
        {loaded ? 'Refresh videos' : 'Load channel videos'}
      </button>
      {loaded && items.length === 0 && (
        <p>This channel has no uploaded videos.</p>
      )}
      {items.map((video) => (
        <div className="channel-video" key={video.id}>
          <span>
            {video.title}
            <small>{video.visibility}</small>
          </span>
          <button
            className="btn btn-secondary"
            disabled={busy}
            onClick={() => {
              setEdit(video);
              setCopy({
                title: video.title,
                description: video.description,
                tags: video.tags.join(', '),
              });
              setConfirm(false);
              setError('');
              setMessage('');
            }}
          >
            Edit metadata
          </button>
        </div>
      ))}
      {next && (
        <button
          className="btn btn-secondary"
          disabled={busy}
          onClick={() => load(true)}
        >
          Load more videos
        </button>
      )}
      {edit && (
        <form onSubmit={save}>
          <h3>Edit YouTube metadata</h3>
          <CopyFields
            copy={copy}
            change={(v) => {
              setCopy(v);
              setConfirm(false);
            }}
          />
          <label className="check-field">
            <input
              type="checkbox"
              checked={confirm}
              required
              onChange={(e) => setConfirm(e.target.checked)}
            />
            Apply these changes to this video on YouTube.
          </label>
          <div className="channel-actions">
            <button className="btn btn-primary" disabled={!confirm || busy || descriptionBytes(copy.description) > DESCRIPTION_BYTE_LIMIT}>
              Save on YouTube
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={busy}
              onClick={() => setEdit(null)}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
      {message && <p role="status">{message}</p>}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </section>
  );
}

function readSaved(key: string): SavedUpload | null {
  try {
    return JSON.parse(sessionStorage.getItem(key) || 'null');
  } catch {
    return null;
  }
}

async function fileHash(file: File) {
  const hash = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return Array.from(new Uint8Array(hash), (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('');
}

function Publish({
  channelId,
  reportId,
}: {
  channelId: string;
  reportId: string | null;
}) {
  const { user } = useSession();
  const storage = 'framewise-upload/' + user?.id + '/' + channelId;
  const [saved, setSaved] = useState<SavedUpload | null>(() =>
    readSaved(storage),
  );
  const [copy, setCopy] = useState<Copy>(emptyCopy),
    [draft, setDraft] = useState<Draft | null>(null);
  const [file, setFile] = useState<File | null>(null),
    [visibility, setVisibility] = useState('private'),
    [kids, setKids] = useState('');
  const [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false),
    [progress, setProgress] = useState(0);
  const [error, setError] = useState(''),
    [result, setResult] = useState<UploadResult | null>(null);
  useEffect(() => {
    if (!reportId) return;
    const controller = new AbortController();
    request<Draft>(
      '/api/analyses/' + encodeURIComponent(reportId) + '/metadata',
      { signal: controller.signal },
    )
      .then((d) => {
        if (controller.signal.aborted) return;
        if (!d.draft || d.stale)
          throw new Error(
            'Refresh the metadata draft in its report before publishing.',
          );
        setDraft(d);
        setCopy({
          title: d.draft.titles[0]?.text || '',
          description: d.draft.descriptions[0]?.text || '',
          tags: d.draft.tags.map((t) => t.text).join(', '),
        });
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : errorText(e));
      });
    return () => controller.abort();
  }, [reportId]);
  function remember(v: SavedUpload | null) {
    setSaved(v);
    try {
      if (v) sessionStorage.setItem(storage, JSON.stringify(v));
      else sessionStorage.removeItem(storage);
    } catch {
      /* current tab still supports retry */
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || !confirmed || busy) return;
    if (!saved && descriptionBytes(copy.description) > DESCRIPTION_BYTE_LIMIT) {
      setError('Shorten the YouTube description to 5,000 bytes.');
      return;
    }
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const mime = validTypes[file.name.split('.').pop()?.toLowerCase() || ''];
      if (!mime || file.size === 0 || file.size > 100 * 1024 * 1024)
        throw new Error('Choose a supported video up to 100 MiB.');
      const hash = await fileHash(file);
      if (saved && hash !== saved.hash)
        throw new Error('Select the same source video to resume this upload.');
      let task: UploadResult;
      if (saved)
        task = await request<UploadResult>(base + '/uploads/' + saved.id);
      else {
        const id = crypto.randomUUID();
        task = await jsonRequest<UploadResult>(base + '/uploads', 'POST', {
          ...values(copy),
          request_id: id,
          size: file.size,
          mime,
          visibility,
          made_for_kids: kids === 'yes',
          confirmed: true,
          ...(reportId && draft
            ? {
                report_id: reportId,
                metadata_revision: draft.revision,
                transcript_revision: draft.transcript_revision,
              }
            : {}),
        });
        remember({ id: task.id, hash, title: copy.title, visibility });
      }
      while (!task.video_id) {
        const start = task.offset,
          end = Math.min(start + chunkSize, file.size);
        if (end <= start)
          throw new Error(
            'YouTube has not confirmed the upload. Resume to check its status.',
          );
        const updated = await request<UploadResult>(
          base + '/uploads/' + task.id,
          {
            method: 'PUT',
            headers: {
              'Content-Type': 'application/octet-stream',
              'Content-Range': `bytes ${start}-${end - 1}/${file.size}`,
            },
            body: file.slice(start, end),
          },
        );
        if (!updated.video_id && updated.offset === start)
          throw new Error(
            'YouTube did not acknowledge this chunk. Resume the upload.',
          );
        task = updated;
        setProgress(Math.round((100 * task.offset) / file.size));
      }
      setResult(task);
      remember(null);
      setConfirmed(false);
      setFile(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="channel-panel">
      <div className="channel-heading">
        <h2>{saved ? 'Resume upload' : 'Upload to YouTube'}</h2>
        <UploadCloud aria-hidden="true" />
      </div>
      <form onSubmit={submit}>
        <fieldset disabled={busy}>
          <label className="field">
            <span>Source video</span>
            <input
              type="file"
              accept=".mp4,.m4v,.mov,.webm,.mkv"
              required
              onChange={(e) => {
                setFile(e.target.files?.[0] || null);
                setConfirmed(false);
              }}
            />
            <small>
              Select a video up to 100 MiB. Analysis uploads are deleted after
              processing.
            </small>
          </label>
          {saved ? (
            <p>
              Resume “{saved.title}” as <strong>{saved.visibility}</strong> with
              the original source file.
            </p>
          ) : (
            <>
              {reportId && (
                <p className="subtle">
                  <Link to={'/analyses/' + encodeURIComponent(reportId)}>
                    Source report
                  </Link>
                  {draft ? ' · Review your saved metadata draft.' : ''}
                </p>
              )}
              {draft?.draft && (
                <SavedChoices
                  draft={draft.draft}
                  copy={copy}
                  change={(value) => {
                    setCopy(value);
                    setConfirmed(false);
                  }}
                />
              )}
              <CopyFields
                copy={copy}
                change={(v) => {
                  setCopy(v);
                  setConfirmed(false);
                }}
              />
              <div className="channel-controls">
                <label className="field">
                  <span>Visibility</span>
                  <select
                    value={visibility}
                    onChange={(e) => {
                      setVisibility(e.target.value);
                      setConfirmed(false);
                    }}
                  >
                    <option value="private">Private</option>
                    <option value="unlisted">Unlisted</option>
                    <option value="public">Public</option>
                  </select>
                </label>
                <label className="field">
                  <span>Is this video made for kids?</span>
                  <select
                    value={kids}
                    required
                    onChange={(e) => {
                      setKids(e.target.value);
                      setConfirmed(false);
                    }}
                  >
                    <option value="">Choose audience</option>
                    <option value="no">No</option>
                    <option value="yes">Yes</option>
                  </select>
                </label>
              </div>
            </>
          )}
          <label className="check-field">
            <input
              type="checkbox"
              checked={confirmed}
              required
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            Upload this video to my channel with{' '}
            {saved?.visibility || visibility} visibility.
          </label>
          <div className="channel-actions">
            <button
              className="btn btn-primary"
              disabled={
                !confirmed ||
                !file ||
                (!saved &&
                  (descriptionBytes(copy.description) > DESCRIPTION_BYTE_LIMIT || (!!reportId && !draft)))
              }
            >
              {saved ? 'Resume upload' : 'Upload video'}
            </button>
            {saved && (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => {
                  remember(null);
                  setConfirmed(false);
                  setProgress(0);
                }}
              >
                Forget this transfer
              </button>
            )}
          </div>
        </fieldset>
      </form>
      {busy && <p role="status">Uploading · {progress}%</p>}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {result?.video_id && (
        <p role="status">
          YouTube accepted your video
          {result.visibility ? ' as ' + result.visibility : ''}.{' '}
          <a
            href={
              'https://www.youtube.com/watch?v=' +
              encodeURIComponent(result.video_id)
            }
            target="_blank"
            rel="noreferrer"
          >
            View video
          </a>
        </p>
      )}
    </section>
  );
}

export default function Channel() {
  const { user } = useSession();
  const userId = user?.id;
  const [params] = useSearchParams(),
    [connection, setConnection] = useState<Connection | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [removing, setRemoving] = useState(false);
  const [disconnected, setDisconnected] = useState('');
  const feedback =
    disconnected || oauthMessages[params.get('connection') || ''];
  const refresh = () =>
    request<Connection>(base)
      .then(setConnection)
      .catch((e) => setError(errorText(e)));
  useEffect(() => {
    void refresh();
  }, []);
  async function action(remove: boolean) {
    setBusy(true);
    setError('');
    try {
      if (remove) {
        const result = await request<{ revocation: string }>(base, {
          method: 'DELETE',
        });
        setRemoving(false);
        if (connection?.channel)
          sessionStorage.removeItem(
            'framewise-upload/' + userId + '/' + connection.channel.id,
          );
        setDisconnected(
          result.revocation === 'unconfirmed'
            ? 'Local channel data was removed. Open your Google Account permissions to finish revoking access.'
            : 'Channel disconnected and its local data removed.',
        );
        await refresh();
      } else {
        const report = params.get('report');
        const route =
          base +
          '/oauth/start' +
          (report ? '?report=' + encodeURIComponent(report) : '');
        const result = await request<{ url: string }>(route, {
          method: 'POST',
        });
        window.location.assign(result.url);
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="channel-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            <span className="eyebrow-rule" />
            Publishing
          </p>
          <h1>
            YouTube channel<span className="heading-dot">.</span>
          </h1>
          <p className="subtle">
            View channel performance, edit video metadata, and publish your next
            upload.
          </p>
        </div>
      </div>
      {error && (
        <div role="alert" className="form-error">
          {error}{' '}
          <button className="text-link" onClick={refresh}>
            Retry
          </button>
        </div>
      )}
      {feedback && <p role="status">{feedback}</p>}
      {!connection && !error && (
        <p className="loading-state" role="status">
          Loading connection…
        </p>
      )}
      {connection && (
        <section className="channel-panel">
          <div className="channel-heading">
            <h2>{connection.channel?.title || 'Connect your channel'}</h2>
            <Link2 aria-hidden="true" />
          </div>
          {connection.channel ? (
            <>
              {removing ? (
                <>
                  <p>
                    Disconnect this channel, remove its local data, and revoke
                    this app’s Google access?
                  </p>
                  <div className="channel-actions">
                    <button
                      className="btn btn-primary"
                      disabled={busy}
                      onClick={() => action(true)}
                    >
                      Confirm disconnect
                    </button>
                    <button
                      className="btn btn-secondary"
                      disabled={busy}
                      onClick={() => setRemoving(false)}
                    >
                      Cancel
                    </button>
                  </div>
                </>
              ) : (
                <div className="channel-actions">
                  <button
                    className="btn btn-secondary"
                    disabled={busy}
                    onClick={() => setRemoving(true)}
                  >
                    Disconnect
                  </button>
                  {connection.configured && (
                    <button
                      className="btn btn-secondary"
                      disabled={busy}
                      onClick={() => action(false)}
                    >
                      Reconnect with Google
                    </button>
                  )}
                </div>
              )}
            </>
          ) : !connection.configured ? (
            <p className="subtle">
              YouTube connection is unavailable on this installation. The server
              needs Google OAuth credentials and a channel encryption key.
            </p>
          ) : (
            <>
              <p className="subtle">
                Approve access in Google to read channel analytics and manage
                your YouTube videos.
              </p>
              <button
                className="btn btn-primary"
                onClick={() => action(false)}
                disabled={busy}
              >
                Connect with Google
              </button>
            </>
          )}
          <p className="subtle">
            <Link to="/privacy">How channel data is used</Link>
            {disconnected && (
              <>
                {' '}
                ·{' '}
                <a
                  href="https://myaccount.google.com/connections"
                  target="_blank"
                  rel="noreferrer"
                >
                  Google Account permissions
                </a>
              </>
            )}
          </p>
        </section>
      )}
      {connection?.configured && connection.channel && (
        <>
          <AnalyticsPanel channel={connection.channel} />
          <Publish
            key={connection.channel.id + (params.get('report') || '')}
            channelId={connection.channel.id}
            reportId={params.get('report')}
          />
          <VideoLibrary />
        </>
      )}
    </div>
  );
}
