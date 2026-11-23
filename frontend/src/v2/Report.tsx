import { useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  Check,
  Clock3,
  Copy,
  Download,
  FileText,
  Film,
  Play,
  Quote,
  RotateCw,
  Tag,
} from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { errorText, isRunning, reportDate, request, timestamp, type Report } from './api';
import { getSample } from './data';
import { evidenceSegmentIndex, filenameFor, keywordCsv } from './report-utils';
import { StatusBadge } from './Workspace';
import Research from './Research';
import SampleMetadata from './SampleMetadata';
import { MetadataEditor, TranscriptEditor } from './Editor';

const reportTabs = [
  { id: 'keywords', label: 'Keywords', icon: Tag },
  { id: 'transcript', label: 'Transcript', icon: FileText },
  { id: 'research', label: 'YouTube research', icon: null },
  { id: 'metadata', label: 'AI metadata', icon: null },
] as const;
type ReportTab = (typeof reportTabs)[number]['id'];

function Highlight({ text, phrase }: { text: string; phrase: string }) {
  const index = text.toLowerCase().indexOf(phrase.toLowerCase());
  if (index < 0 || !phrase) return <>{text}</>;
  return (
    <>
      {text.slice(0, index)}
      <mark>{text.slice(index, index + phrase.length)}</mark>
      {text.slice(index + phrase.length)}
    </>
  );
}

export default function ReportPage({ sample = false }: { sample?: boolean }) {
  const { id } = useParams();
  const [report, setReport] = useState<Report | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState('');
  const [retrying, setRetrying] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setReport(null);
    setError('');
    async function load() {
      try {
        const fresh = sample
          ? await getSample()
          : await request<Report>('/api/analyses/' + encodeURIComponent(id ?? ''), {
              signal: controller.signal,
            });
        if (controller.signal.aborted) return;
        setReport(fresh);
        if (!sample && isRunning(fresh.status)) timer = setTimeout(() => void load(), 1600);
      } catch (reason) {
        if (!controller.signal.aborted) setError(errorText(reason));
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [sample, id, attempt]);
  async function retryJob() {
    if (retrying || !id) return;
    setRetrying(true);
    setError('');
    try {
      await request<Report>('/api/analyses/' + encodeURIComponent(id) + '/retry', {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      setAttempt((n) => n + 1);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setRetrying(false);
    }
  }
  if (error)
    return (
      <div className="empty-state">
        <h2>Couldn’t load this report</h2>
        <p role="alert">{error}</p>
        <div className="form-actions">
          <button className="btn btn-secondary" onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </button>
          <Link className="btn btn-ghost" to="/analyses">
            Back to library
          </Link>
        </div>
      </div>
    );
  if (!report)
    return (
      <p className="loading-state" role="status">
        Opening report…
      </p>
    );
  if (report.status === 'completed')
    return (
      <Completed
        key={report.id}
        report={report}
        sample={sample}
        onRefresh={() => setAttempt((n) => n + 1)}
      />
    );
  return (
    <>
      <Link to="/analyses" className="back-link">
        <ArrowLeft size={15} />
        Analysis library
      </Link>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Analysis report</p>
          <h1>{report.title}</h1>
        </div>
        <StatusBadge status={report.status} />
      </div>
      <div className="surface job-progress" aria-live="polite">
        {report.status === 'failed' ? (
          <>
            <h2>Analysis failed</h2>
            <p>{report.error || 'The analysis could not be completed.'}</p>
            <div className="form-actions">
              <button
                className="btn btn-primary"
                disabled={retrying}
                onClick={() => void retryJob()}
              >
                <RotateCw size={15} />
                {retrying ? 'Retrying…' : 'Retry analysis'}
              </button>
              <Link to="/new" className="btn btn-secondary">
                Choose another source
              </Link>
            </div>
          </>
        ) : (
          <>
            <p className="eyebrow">In progress</p>
            <h2>{report.stage}</h2>
            <progress max={100} value={report.progress} aria-label="Analysis progress" />
            <p>You can leave this page and return through your analysis library.</p>
          </>
        )}
      </div>
    </>
  );
}

function intervalTime(seconds: number) {
  const milliseconds = Math.round(seconds * 1000);
  const fraction = String(milliseconds % 1000)
    .padStart(3, '0')
    .replace(/0+$/, '');
  return timestamp(Math.floor(milliseconds / 1000)) + (fraction ? '.' + fraction : '');
}

function Completed({
  report,
  sample,
  onRefresh,
}: {
  report: Report;
  sample: boolean;
  onRefresh: () => void;
}) {
  const [tab, setTab] = useState<ReportTab>('keywords');
  const [selected, setSelected] = useState(0);
  const [actionMessage, setActionMessage] = useState('');
  const video = useRef<HTMLVideoElement>(null);
  const [copied, setCopied] = useState(false);
  const [segmentIndex, setSegmentIndex] = useState(-1);
  const keyword = report.keywords[selected];
  const coverage =
    !sample && report.source_type === 'video' && report.speech_coverage?.partial
      ? report.speech_coverage
      : null;
  useEffect(() => {
    document.title = report.title + ' · AI Video SEO Analysis System';
  }, [report.title]);

  function seek(seconds: number) {
    if (!video.current) return;
    const player = video.current;
    const start = () => {
      player.currentTime = seconds;
      void player.play().catch(() => {
        setActionMessage('Video positioned at ' + timestamp(seconds) + '. Press play to listen.');
      });
    };
    if (player.readyState > 0) start();
    else {
      player.addEventListener('loadedmetadata', start, { once: true });
      player.load();
    }
    player.scrollIntoView({ block: 'nearest' });
    player.focus({ preventScroll: true });
  }
  function showExcerpt() {
    if (!keyword) return;
    if (sample && keyword.start_seconds != null) {
      seek(keyword.start_seconds);
      return;
    }
    const found = evidenceSegmentIndex(report.segments, keyword);
    setSegmentIndex(found);
    setTab('transcript');
    requestAnimationFrame(() =>
      document.getElementById('segment-' + found)?.scrollIntoView({ block: 'center' }),
    );
  }
  async function copyKeywords() {
    try {
      await navigator.clipboard.writeText(report.keywords.map((k) => k.phrase).join(', '));
      setCopied(true);
      setActionMessage('Keywords copied to clipboard.');
    } catch {
      setActionMessage('Clipboard access was unavailable. You can export the keywords as CSV.');
    }
  }
  function exportCsv() {
    const url = URL.createObjectURL(
      new Blob([keywordCsv(report.keywords)], { type: 'text/csv;charset=utf-8' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = filenameFor(report.title);
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setActionMessage('CSV export started.');
  }

  return (
    <>
      <Link to="/analyses" className="back-link">
        <ArrowLeft size={15} />
        Analysis library
      </Link>
      <div className="page-heading report-heading">
        <div>
          <p className="eyebrow">{sample ? 'Sample report' : 'Analysis report'}</p>
          <h1>{report.title}</h1>
          <div className="report-meta">
            <span>
              <FileText size={15} />
              {report.word_count.toLocaleString()} words
            </span>
            <span>
              <Tag size={15} />
              {report.keyword_count} keywords
            </span>
            <span>{reportDate(report.created_at)}</span>
          </div>
          {coverage && (
            <p className="subtle">
              <strong>Partial transcript.</strong> Speech was recognized in{' '}
              {coverage.recognized_chunks} of {coverage.total_chunks} audio intervals. Keywords
              cover the recognized passages.
            </p>
          )}
        </div>
        <div className="report-toolbar">
          <button
            className="btn btn-secondary"
            disabled={!report.keywords.length}
            onClick={exportCsv}
          >
            <Download size={16} />
            Export CSV
          </button>
        </div>
      </div>
      {sample && (
        <section className="report-intro surface">
          <div className="report-video">
            <video
              className="sample-player"
              ref={video}
              controls
              preload="metadata"
              playsInline
              poster="/studio-still.jpg"
              tabIndex={0}
              aria-label="A field guide to alpine photography, narrated sample"
            >
              <source src="/sample.mp4" type="video/mp4" />
              <track kind="captions" src="/sample.vtt" srcLang="en" label="English" />
              Your browser does not support embedded video.
            </video>
          </div>
          <div className="report-overview">
            <p className="eyebrow">
              <Film size={15} />
              Source video
            </p>
            <h2>The alpine field guide</h2>
            <p>
              Watch the narrated field guide, then select a keyword to hear the passage that
              supports it.
            </p>
            {report.duration_seconds != null && (
              <span className="report-meta">
                <Clock3 size={15} />
                {timestamp(report.duration_seconds)} duration
              </span>
            )}
          </div>
        </section>
      )}
      <div className="report-tabs tabs" role="group" aria-label="Report view">
        {reportTabs.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={'tab' + (tab === id ? ' active' : '')}
            aria-pressed={tab === id}
            onClick={() => setTab(id)}
          >
            {Icon && <Icon size={15} />}
            {label}
          </button>
        ))}
      </div>
      {tab === 'metadata' && (
        sample ? (
          <SampleMetadata report={report} play={seek} />
        ) : (
          <MetadataEditor report={report} onRefresh={onRefresh} />
        )
      )}
      {tab === 'research' && (
        <section className="research-view">
          <label className="field">
            <span>Research keyword</span>
            <select value={selected} onChange={(event) => setSelected(Number(event.target.value))}>
              {report.keywords.map((entry, i) => (
                <option key={entry.phrase} value={i}>
                  {entry.phrase}
                </option>
              ))}
            </select>
          </label>
          {keyword ? (
            <Research
              key={keyword.phrase}
              reportId={report.id}
              phrase={keyword.phrase}
              sample={sample}
            />
          ) : (
            <p>This transcript has no extracted keywords.</p>
          )}
        </section>
      )}
      {tab === 'keywords' && (
        <div className="detail-grid">
          <section className="surface report-keywords">
            <div className="panel-heading">
              <h2>Keyword evidence</h2>
              <button
                className="icon-button"
                disabled={!report.keywords.length}
                aria-label="Copy keywords"
                title="Copy keywords"
                onClick={() => void copyKeywords()}
              >
                {copied ? <Check size={16} /> : <Copy size={16} />}
              </button>
            </div>
            <p className="subtle">
              The strongest phrase scores 100 in this transcript. Mentions count how often each
              phrase appears.
            </p>
            {report.keywords.length ? (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th scope="col">Keyword</th>
                      <th scope="col">Transcript relevance</th>
                      <th scope="col">Mentions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.keywords.map((entry, index) => (
                      <tr
                        className={'keyword-row' + (index === selected ? ' active' : '')}
                        key={entry.phrase}
                      >
                        <th scope="row">
                          <button
                            className="keyword-select"
                            aria-pressed={index === selected}
                            onClick={() => setSelected(index)}
                          >
                            {entry.phrase}
                          </button>
                        </th>
                        <td>{entry.score}</td>
                        <td>{entry.occurrences}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="empty-state">
                No keywords met the extraction criteria for this transcript.
              </p>
            )}
          </section>
          {keyword && (
            <aside className="panel evidence-panel" aria-live="polite">
              <p className="eyebrow">
                <Quote size={14} />
                Transcript evidence
              </p>
              <h2>{keyword.phrase}</h2>
              <blockquote className="evidence-quote">
                <Highlight text={keyword.evidence} phrase={keyword.phrase} />
              </blockquote>
              <button className="btn btn-secondary" onClick={showExcerpt}>
                {sample && keyword.start_seconds != null ? (
                  <>
                    <Play size={14} />
                    Play at {timestamp(keyword.start_seconds)}
                  </>
                ) : (
                  <>
                    <FileText size={15} />
                    Read in transcript
                  </>
                )}
              </button>
              <button className="btn btn-primary research-cta" onClick={() => setTab('research')}>
                Research on YouTube
              </button>
            </aside>
          )}
        </div>
      )}
      {tab === 'transcript' && (
        <section className="surface report-transcript">
          <div className="panel-heading">
            <h2>Transcript</h2>
            <span className="subtle">{report.word_count.toLocaleString()} words</span>
          </div>
          {!sample && <TranscriptEditor report={report} onRefresh={onRefresh} />}
          {coverage && (
            <aside className="segment coverage-note" aria-label="Transcription gaps">
              <strong>Missing speech intervals</strong>
              <p>
                {coverage.unrecognized_ranges?.length
                  ? 'Speech could not be recognized during ' +
                    coverage.unrecognized_ranges
                      .map(
                        (range) =>
                          intervalTime(range.start_seconds) + '–' + intervalTime(range.end_seconds),
                      )
                      .join(', ') +
                    '.'
                  : 'Some audio intervals contained no recognizable speech.'}
              </p>
            </aside>
          )}
          {report.segments.length ? (
            report.segments.map((part, index) => (
              <div
                className={'segment' + (segmentIndex === index ? ' highlighted' : '')}
                key={index}
                id={'segment-' + index}
              >
                {part.start_seconds != null &&
                  (sample ? (
                    <button
                      className="segment-time"
                      onClick={() => seek(part.start_seconds!)}
                      aria-label={'Play at ' + timestamp(part.start_seconds)}
                    >
                      <Play size={12} />
                      {timestamp(part.start_seconds)}
                    </button>
                  ) : (
                    <span className="segment-time">{timestamp(part.start_seconds)}</span>
                  ))}
                <p>
                  <Highlight phrase={keyword?.phrase ?? ''} text={part.text} />
                </p>
              </div>
            ))
          ) : (
            <p className="transcript-text">{report.transcript}</p>
          )}
        </section>
      )}
      <p className="report-feedback" role="status">
        {actionMessage}
      </p>
    </>
  );
}
