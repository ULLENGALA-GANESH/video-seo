import { useEffect, useState, type FormEvent } from 'react';
import { ExternalLink, Search } from 'lucide-react';
import { errorText, jsonRequest, request, reportDate } from './api';
import './research.css';

type Props = { reportId: string; phrase: string; sample: boolean };
type Level = 'unrated' | 'low' | 'moderate' | 'high';
type Assessment = { level: Level; notes: string; updated_at: string | null };
type Video = {
  video_id: string;
  title: string;
  channel: string;
  published_at: string | null;
  url: string;
  views: number | null;
  likes: number | null;
  comments: number | null;
};
type Snapshot = {
  phrase: string;
  region: string;
  collected_at: string;
  cached: boolean;
  videos: Video[];
};
const REGIONS = {
  US: 'United States',
  IN: 'India',
  GB: 'United Kingdom',
  CA: 'Canada',
  AU: 'Australia',
  DE: 'Germany',
};
const number = new Intl.NumberFormat();
const formatCount = (value: number | null) =>
  value == null ? 'Unavailable' : number.format(value);
const readableTime = (value: string) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'long',
  }).format(new Date(value));

export default function Research(props: Props) {
  const [region, setRegion] = useState<keyof typeof REGIONS>('US');
  return (
    <section className="keyword-research" aria-label="YouTube keyword research">
      <div className="research-heading">
        <div>
          <p className="eyebrow">YouTube research</p>
          <h2>{props.phrase}</h2>
        </div>
        <label className="field research-region">
          <span>Video availability region</span>
          <select
            value={region}
            onChange={(e) => setRegion(e.target.value as keyof typeof REGIONS)}
          >
            {Object.entries(REGIONS).map(([code, name]) => (
              <option key={code} value={code}>
                {name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <RegionResearch
        key={JSON.stringify([props.reportId, props.phrase, region, props.sample])}
        {...props}
        region={region}
      />
    </section>
  );
}

function RegionResearch({
  reportId,
  phrase,
  sample,
  region,
}: Props & { region: keyof typeof REGIONS }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function lookUp() {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const path = sample ? '/api/research/sample' : '/api/research';
      const result = await jsonRequest<Snapshot>(path, 'POST', {
        report_id: reportId,
        phrase,
        region,
      });
      setSnapshot(result);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <p className="subtle research-description">
        Find up to 10 relevant videos available in {REGIONS[region]}. This sends the selected phrase
        and region to YouTube. A previous lookup can be reused for six hours.
      </p>
      <button
        className="btn btn-secondary"
        type="button"
        disabled={busy}
        onClick={() => void lookUp()}
      >
        <Search size={15} />
        {busy ? 'Looking up videos…' : snapshot ? 'Check snapshot' : 'Look up on YouTube'}
      </button>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {snapshot && (
        <div className="research-snapshot">
          <div className="research-snapshot-heading">
            <h3>YouTube results</h3>
            <span className="subtle">
              {snapshot.cached ? 'Cached snapshot' : 'Live snapshot'} · {snapshot.region} ·{' '}
              <time dateTime={snapshot.collected_at}>{readableTime(snapshot.collected_at)}</time>
            </span>
          </div>
          {snapshot.videos.length ? (
            <ul className="research-videos">
              {snapshot.videos.map((video) => (
                <li key={video.video_id}>
                  <a
                    className="research-video-title"
                    href={video.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {video.title}
                    <ExternalLink size={13} aria-label="Opens in a new tab" />
                  </a>
                  <p className="subtle">
                    {video.channel}
                    {video.published_at && <> · Published {reportDate(video.published_at)}</>}
                  </p>
                  <dl className="research-counts">
                    <div>
                      <dt>Views</dt>
                      <dd>{formatCount(video.views)}</dd>
                    </div>
                    <div>
                      <dt>Likes</dt>
                      <dd>{formatCount(video.likes)}</dd>
                    </div>
                    <div>
                      <dt>Comments</dt>
                      <dd>{formatCount(video.comments)}</dd>
                    </div>
                  </dl>
                </li>
              ))}
            </ul>
          ) : (
            <p role="status">YouTube returned no available video details for this lookup.</p>
          )}
          <p className="subtle research-source">
            Counts are public video statistics from the{' '}
            <a
              href="https://developers.google.com/youtube/v3/docs/videos"
              target="_blank"
              rel="noreferrer"
            >
              YouTube Data API
            </a>
            . Keyword search volume and organic difficulty are unavailable from this API.
          </p>
        </div>
      )}
      {sample ? (
        <p className="research-assessment-hint subtle">
          Save a personal competition assessment in a report from your workspace.
        </p>
      ) : (
        <PersonalAssessment reportId={reportId} phrase={phrase} region={region} />
      )}
    </>
  );
}

function PersonalAssessment({
  reportId,
  phrase,
  region,
}: Omit<Props, 'sample'> & { region: string }) {
  const [saved, setSaved] = useState<Assessment | null>(null);
  const [level, setLevel] = useState<Level>('unrated');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    request<Assessment>('/api/research/assessment/read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ report_id: reportId, phrase, region }),
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) {
          setSaved(value);
          setLevel(value.level);
          setNotes(value.notes);
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorText(reason));
      });
    return () => controller.abort();
  }, [reportId, phrase, region, attempt]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const value = await jsonRequest<Assessment>('/api/research/assessment', 'PUT', {
        report_id: reportId,
        phrase,
        region,
        level,
        notes,
      });
      setSaved(value);
      setLevel(value.level);
      setNotes(value.notes);
      setMessage(value.updated_at ? 'Assessment saved.' : 'Assessment cleared.');
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setSaving(false);
    }
  }
  return (
    <section className="research-assessment">
      <h3>Your competition assessment</h3>
      <p className="subtle">
        Record your editorial judgment for this keyword and region. YouTube does not supply this
        rating. Leave it unrated when evidence is limited.
      </p>
      {!saved && !error && <p role="status">Loading assessment…</p>}
      {error && (
        <p className="form-error" role="alert">
          {error}{' '}
          {!saved && (
            <button className="text-link" type="button" onClick={() => setAttempt((a) => a + 1)}>
              Try again
            </button>
          )}
        </p>
      )}
      {saved && (
        <form onSubmit={save} className="form-stack">
          <label className="field">
            <span>Competition</span>
            <select
              value={level}
              onChange={(e) => {
                setLevel(e.target.value as Level);
                setMessage('');
              }}
            >
              <option value="unrated">Unrated</option>
              <option value="low">Low</option>
              <option value="moderate">Moderate</option>
              <option value="high">High</option>
            </select>
          </label>
          <label className="field">
            <span>Reasoning and sources</span>
            <textarea
              rows={3}
              maxLength={1000}
              value={notes}
              onChange={(e) => {
                setNotes(e.target.value);
                setMessage('');
              }}
              placeholder="What informed your judgment?"
            />
          </label>
          <div className="form-actions">
            <button
              type="submit"
              className="btn btn-secondary"
              disabled={saving || (level === saved.level && notes.trim() === saved.notes)}
            >
              {saving ? 'Saving…' : 'Save assessment'}
            </button>
            {saved.updated_at && (
              <small className="subtle">Last saved {readableTime(saved.updated_at)}</small>
            )}
          </div>
          {message && (
            <p className="form-success" role="status">
              {message}
            </p>
          )}
        </form>
      )}
    </section>
  );
}
