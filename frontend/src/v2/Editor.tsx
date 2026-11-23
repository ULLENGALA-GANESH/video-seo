import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { errorText, jsonRequest, request, timestamp, type Report } from './api';

type EditorProps = { report: Report; onRefresh: () => void };
const endpoint = (id: string) => '/api/analyses/' + encodeURIComponent(id);

export function TranscriptEditor({ report, onRefresh }: EditorProps) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(report.transcript);
  const [passages, setPassages] = useState(report.segments.map((part) => part.text));
  const [selected, setSelected] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const timed = report.segments.some((part) => part.start_seconds != null);
  const changed = timed
    ? passages.some((part, i) => part !== report.segments[i].text)
    : text !== report.transcript;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !changed) return;
    setBusy(true);
    setError('');
    try {
      await jsonRequest(endpoint(report.id) + '/transcript', 'PUT', {
        transcript_revision: report.transcript_revision ?? 0,
        ...(timed ? { segments: passages } : { transcript: text }),
      });
      setOpen(false);
      onRefresh();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }
  if (!open)
    return (
      <button className="btn btn-secondary" onClick={() => setOpen(true)}>
        Correct transcript
      </button>
    );
  return (
    <form className="surface workspace-form form-stack" onSubmit={submit}>
      <h2>Correct transcript</h2>
      {timed && (
        <label className="field">
          <span>Audio interval</span>
          <select value={selected} onChange={(event) => setSelected(Number(event.target.value))}>
            {report.segments.map((part, i) => (
              <option value={i} key={i}>
                Passage {i + 1}
                {part.start_seconds != null && ' · ' + timestamp(part.start_seconds)}
                {part.end_seconds != null && '–' + timestamp(part.end_seconds)}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="field">
        <span>{timed ? 'Passage text' : 'Transcript text'}</span>
        <textarea
          required
          maxLength={60000}
          rows={8}
          value={timed ? passages[selected] : text}
          onChange={(event) => {
            const value = event.target.value;
            if (timed)
              setPassages((parts) => parts.map((part, i) => (i === selected ? value : part)));
            else setText(value);
          }}
        />
      </label>
      <p className="subtle">
        Saving recalculates keyword evidence
        {timed ? ' and preserves the original audio intervals' : ''}. Existing metadata drafts will
        need review.
      </p>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <div className="form-actions">
        <button disabled={busy || !changed} className="btn btn-primary">
          {busy ? 'Reanalyzing…' : 'Save and reanalyze'}
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          disabled={busy}
          onClick={() => {
            setText(report.transcript);
            setPassages(report.segments.map((part) => part.text));
            setError('');
            setOpen(false);
          }}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

const groups = ['titles', 'descriptions', 'tags', 'chapters'] as const;
type Group = (typeof groups)[number];
type Suggestion = { text: string; reason: string; segment_index: number };
type Fields = Record<Group, Suggestion[]>;
type Stored = Fields & {
  model: string;
  source_revision: number;
  omitted_count?: number;
  evidence: Record<string, { text: string; start_seconds: number | null }>;
};
type Metadata = {
  revision: number;
  transcript_revision: number;
  stale: boolean;
  draft: Stored | null;
};
const limits = { titles: 100, descriptions: 5000, tags: 80, chapters: 140 };
const maxItems = { titles: 4, descriptions: 2, tags: 15, chapters: 12 };

function editable(draft: Stored | null): Fields | null {
  if (!draft) return null;
  return {
    titles: draft.titles,
    descriptions: draft.descriptions,
    tags: draft.tags,
    chapters: draft.chapters,
  };
}

function download(data: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
  );
  const link = document.createElement('a');
  link.href = url;
  link.download = 'video-seo-metadata.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function MetadataEditor({ report, onRefresh }: EditorProps) {
  const [state, setState] = useState<Metadata | null>(null);
  const [fields, setFields] = useState<Fields | null>(null);
  const [group, setGroup] = useState<Group>('titles');
  const [busy, setBusy] = useState<'' | 'generating' | 'saving'>('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loadAttempt, setLoadAttempt] = useState(0);
  const path = endpoint(report.id) + '/metadata';
  const dirty = !!state && JSON.stringify(fields) !== JSON.stringify(editable(state.draft));
  const stale =
    state?.stale || (!!state && state.transcript_revision !== (report.transcript_revision ?? 0));
  const source = report.segments.length
    ? report.segments
    : [{ text: report.transcript, start_seconds: null }];

  useEffect(() => {
    const controller = new AbortController();
    setState(null);
    setFields(null);
    setMessage('');
    setError('');
    request<Metadata>(path, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) {
          setState(data);
          setFields(editable(data.draft));
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorText(reason));
      });
    return () => controller.abort();
  }, [path, report.transcript_revision, loadAttempt]);

  async function persist(generate: boolean) {
    if (!state || busy) return;
    setBusy(generate ? 'generating' : 'saving');
    setError('');
    setMessage('');
    try {
      const versions = {
        revision: state.revision,
        transcript_revision: report.transcript_revision ?? 0,
      };
      const saved = await jsonRequest<Metadata>(
        generate ? path + '/generate' : path,
        generate ? 'POST' : 'PUT',
        generate ? versions : { ...versions, draft: fields },
      );
      setState(saved);
      setFields(editable(saved.draft));
      setMessage(generate ? 'Draft generated and saved.' : 'Draft changes saved.');
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy('');
    }
  }

  function replace(items: Suggestion[]) {
    if (fields) {
      setFields({ ...fields, [group]: items });
      setMessage('');
    }
  }
  function change(index: number, patch: Partial<Suggestion>) {
    if (fields)
      replace(fields[group].map((item, i) => (i === index ? { ...item, ...patch } : item)));
  }
  async function copy() {
    if (!fields) return;
    const lines = groups
      .map(
        (key) =>
          key.toUpperCase() +
          '\n' +
          fields[key]
            .map((item) => {
              const seconds = source[item.segment_index]?.start_seconds;
              return key === 'chapters' && seconds != null
                ? timestamp(seconds) + ' ' + item.text
                : item.text;
            })
            .join('\n'),
      )
      .join('\n\n');
    try {
      await navigator.clipboard.writeText(lines);
      setMessage('Metadata copied.');
    } catch {
      setError('Clipboard access is unavailable. Use the JSON download.');
    }
  }

  return (
    <section className="surface workspace-form form-stack" aria-label="Metadata draft">
      <div>
        <h2>Metadata draft</h2>
        <p className="subtle">
          Generate titles, descriptions, tags and chapters from transcript passages using the
          server’s local Ollama model.
        </p>
        {(source.length > 16 || source.some((part) => part.text.length > 400)) && (
          <p className="subtle">
            The model uses up to 16 excerpts from across this report, with up to 400 characters per
            passage.
          </p>
        )}
      </div>
      {!state && !error && <p role="status">Loading draft…</p>}
      {busy === 'generating' && (
        <p role="status">Generating from the transcript. Local models can take several minutes.</p>
      )}
      {state && (
        <>
          {stale && (
            <p role="status" className="coverage-note">
              The transcript has changed. Generate a new draft to use the revised passages.
            </p>
          )}
          <button
            type="button"
            className="btn btn-primary"
            disabled={!!busy || dirty}
            onClick={() => void persist(true)}
          >
            {busy === 'generating'
              ? 'Generating locally…'
              : fields
                ? 'Regenerate saved draft'
                : 'Generate metadata draft'}
          </button>
          {fields && state.draft && (
            <>
              <p className="subtle">
                Generated by {state.draft.model} · Transcript revision{' '}
                {state.draft.source_revision + 1}
              </p>
              {!!state.draft.omitted_count && <p className="subtle">
                {state.draft.omitted_count} additional model {state.draft.omitted_count === 1 ? 'suggestion was' : 'suggestions were'} omitted because the cited words could not be verified.
              </p>}
              <div className="tabs" role="group" aria-label="Metadata category">
                {groups.map((key) => (
                  <button
                    type="button"
                    key={key}
                    aria-pressed={key === group}
                    className={'tab' + (key === group ? ' active' : '')}
                    onClick={() => setGroup(key)}
                  >
                    {key[0].toUpperCase() + key.slice(1)} ({fields[key].length})
                  </button>
                ))}
              </div>
              {fields[group].map((item, index) => {
                const citation = stale
                  ? state.draft?.evidence[String(item.segment_index)]
                  : source[item.segment_index];
                return (
                  <fieldset
                    key={index}
                    disabled={!!busy || stale}
                    className="source-field form-stack"
                  >
                    <legend>
                      {group === 'titles'
                        ? 'Title'
                        : group === 'descriptions'
                          ? 'Description'
                          : group === 'tags'
                            ? 'Tag'
                            : 'Chapter'}{' '}
                      {index + 1}
                    </legend>
                    <label className="field">
                      <span>Suggestion</span>
                      <textarea
                        rows={group === 'descriptions' ? 4 : 2}
                        required
                        maxLength={limits[group]}
                        value={item.text}
                        onChange={(event) => change(index, { text: event.target.value })}
                      />
                    </label>
                    <label className="field">
                      <span>Editorial reason</span>
                      <input
                        value={item.reason}
                        required
                        maxLength={500}
                        onChange={(event) => change(index, { reason: event.target.value })}
                      />
                    </label>
                    {!stale && (
                      <label className="field">
                        <span>Supporting passage</span>
                        {source.length > 100 ? (
                          <input
                            type="number"
                            min={1}
                            max={source.length}
                            value={item.segment_index + 1}
                            onChange={(event) => {
                              const n = event.target.valueAsNumber;
                              if (Number.isInteger(n) && n >= 1 && n <= source.length)
                                change(index, { segment_index: n - 1 });
                            }}
                          />
                        ) : (
                          <select
                            value={item.segment_index}
                            onChange={(event) =>
                              change(index, { segment_index: Number(event.target.value) })
                            }
                          >
                            {source.map((part, i) => (
                              <option key={i} value={i}>
                                {i + 1}. {part.text.slice(0, 75)}
                              </option>
                            ))}
                          </select>
                        )}
                      </label>
                    )}
                    {citation && (
                      <blockquote className="evidence-quote">
                        {citation.start_seconds != null &&
                          timestamp(citation.start_seconds) + ' · '}
                        {citation.text.slice(0, 500)}
                      </blockquote>
                    )}
                    <button
                      className="btn btn-ghost"
                      type="button"
                      disabled={fields[group].length <= (group === 'chapters' ? 0 : 1)}
                      onClick={() => replace(fields[group].filter((_, i) => i !== index))}
                    >
                      Remove suggestion
                    </button>
                  </fieldset>
                );
              })}
              <div className="form-actions">
                <button
                  className="btn btn-secondary"
                  disabled={!!busy || stale || fields[group].length >= maxItems[group]}
                  onClick={() =>
                    replace([...fields[group], { text: '', reason: '', segment_index: 0 }])
                  }
                >
                  Add suggestion
                </button>
                <button
                  className="btn btn-primary"
                  disabled={!dirty || !!busy || stale}
                  onClick={() => void persist(false)}
                >
                  {busy === 'saving' ? 'Saving…' : 'Save draft edits'}
                </button>
                {dirty && (
                  <button
                    className="btn btn-ghost"
                    disabled={!!busy}
                    onClick={() => setFields(editable(state.draft))}
                  >
                    Discard edits
                  </button>
                )}
              </div>
              <div className="form-actions">
                <button
                  className="btn btn-secondary"
                  disabled={!!busy || dirty || stale}
                  onClick={() => void copy()}
                >
                  Copy metadata
                </button>
                <button
                  className="btn btn-secondary"
                  disabled={!!busy || dirty}
                  onClick={() => download({ report_id: report.id, ...state })}
                >
                  Download saved JSON
                </button>
                {!busy && !dirty && !stale && (
                  <Link
                    className="btn btn-secondary"
                    to={'/channel?report=' + encodeURIComponent(report.id)}
                  >
                    Prepare YouTube upload
                  </Link>
                )}
              </div>
            </>
          )}
        </>
      )}
      {message && (
        <p role="status" className="form-success">
          {message}
        </p>
      )}
      {error && (
        <>
          <p role="alert" className="form-error">
            {error}
          </p>
          <button
            className="btn btn-ghost"
            disabled={!!busy}
            onClick={() => {
              if (state) onRefresh();
              else setLoadAttempt((attempt) => attempt + 1);
            }}
          >
            Reload saved report
          </button>
        </>
      )}
    </section>
  );
}
