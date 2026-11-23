export type Copy = { title: string; description: string; tags: string };

export type Draft = {
  revision: number;
  transcript_revision: number;
  stale: boolean;
  draft: null | {
    titles: { text: string }[];
    descriptions: { text: string }[];
    tags: { text: string }[];
    chapters: { text: string; segment_index: number }[];
    evidence: Record<string, { start_seconds: number | null }>;
  };
};

export const DESCRIPTION_BYTE_LIMIT = 5000;

export function descriptionBytes(text: string) {
  return new TextEncoder().encode(text).length;
}

function chapterTime(seconds: number) {
  const total = Math.floor(seconds);
  const parts = [Math.floor(total / 60) % 60, total % 60].map((n) =>
    String(n).padStart(2, '0'),
  );
  if (total >= 3600) parts.unshift(String(Math.floor(total / 3600)));
  return parts.join(':');
}

// Compare by time so reapplying suggestions preserves a creator's edited chapter label.
function lineTime(line: string) {
  const result = /^\s*\[?(\d{1,3}:[0-5]\d(?::[0-5]\d)?)\]?(?=\s|$)/.exec(line);
  return result
    ? result[1].split(':').reduce((sum, value) => sum * 60 + Number(value), 0)
    : null;
}

function appendTimedLines(description: string, additions: [number, string][]) {
  const lines: string[] = [];
  const timestamps = new Map<number, string>();
  let insertAt: number | null = null;
  for (const line of description.split(/\r?\n/)) {
    const time = lineTime(line);
    if (time == null) lines.push(line);
    else {
      if (insertAt == null) insertAt = lines.length;
      if (!timestamps.has(time)) timestamps.set(time, line);
    }
  }
  for (const [time, label] of additions) {
    if (!timestamps.has(time))
      timestamps.set(time, chapterTime(time) + ' ' + label);
  }
  const ordered = [...timestamps]
    .sort(([a], [b]) => a - b)
    .map(([, line]) => line);
  if (insertAt == null)
    return [description.trimEnd(), ordered.join('\n')]
      .filter(Boolean)
      .join('\n\n');
  lines.splice(insertAt, 0, ...ordered);
  return lines.join('\n');
}

export function SavedChoices({
  draft,
  copy,
  change,
}: {
  draft: NonNullable<Draft['draft']>;
  copy: Copy;
  change: (value: Copy) => void;
}) {
  const titles = [...new Set(draft.titles.map(({ text }) => text))];
  const descriptions = [...new Set(draft.descriptions.map(({ text }) => text))];
  const chapters = new Map<number, string>();
  for (const item of draft.chapters ?? []) {
    const time = draft.evidence?.[String(item.segment_index)]?.start_seconds;
    const label = item.text
      .replace(/^\s*\[?(?:\d+:)?\d+:[0-5]\d\]?\s+/, '')
      .trim()
      .replace(/\s+/g, ' ');
    if (
      time != null &&
      Number.isFinite(time) &&
      time >= 0 &&
      label &&
      !chapters.has(Math.floor(time))
    ) {
      chapters.set(Math.floor(time), label);
    }
  }
  const existingLines = copy.description.split(/\r?\n/);
  const existingTimes = new Set(existingLines.map(lineTime));
  const timed = [...chapters].sort(([a], [b]) => a - b);
  const missing = timed.filter(([time]) => !existingTimes.has(time));
  const withChapters = appendTimedLines(copy.description, missing);
  const overLimit = descriptionBytes(withChapters) > DESCRIPTION_BYTE_LIMIT;

  function useDescription(description: string) {
    // Retain manually edited chapter names when switching the body of the description.
    const times = new Set(description.split(/\r?\n/).map(lineTime));
    const retained = existingLines.filter((line) => {
      const time = lineTime(line);
      if (time == null || !chapters.has(time) || times.has(time)) return false;
      times.add(time);
      return true;
    });
    change({
      ...copy,
      description: [description.trimEnd(), retained.join('\n')]
        .filter(Boolean)
        .join('\n\n'),
    });
  }
  return (
    <div className="channel-draft-options">
      <h3>Saved draft options</h3>
      <div className="channel-controls">
        {titles.length > 1 && (
          <label className="field">
            <span>Title suggestion</span>
            <select
              value={titles.includes(copy.title) ? copy.title : ''}
              onChange={(e) => change({ ...copy, title: e.target.value })}
            >
              <option value="" disabled>
                Edited title
              </option>
              {titles.map((text) => (
                <option key={text} value={text}>
                  {text}
                </option>
              ))}
            </select>
          </label>
        )}
        {descriptions.length > 1 && (
          <label className="field">
            <span>Description suggestion</span>
            <select
              value={
                descriptions.includes(copy.description) ? copy.description : ''
              }
              onChange={(e) => useDescription(e.target.value)}
            >
              <option value="" disabled>
                Edited description
              </option>
              {descriptions.map((text, index) => (
                <option key={text} value={text}>
                  {index + 1}. {text.slice(0, 100)}
                  {text.length > 100 ? '…' : ''}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {timed.length > 0 ? (
        <>
          <details>
            <summary>Review chapter timestamps ({timed.length})</summary>
            <ul>
              {timed.map(([time, label]) => (
                <li key={time}>
                  <time>{chapterTime(time)}</time> {label}
                </li>
              ))}
            </ul>
          </details>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={!missing.length || overLimit}
            onClick={() => change({ ...copy, description: withChapters })}
          >
            Add chapter timestamps
          </button>
          {!missing.length ? (
            <small>All chapter timestamps are in the description.</small>
          ) : overLimit ? (
            <small>
              Shorten the description to fit the chapter timestamps within 5,000
              bytes.
            </small>
          ) : (
            <small>Check these times against the video you selected.</small>
          )}
        </>
      ) : (
        <small>
          {draft.chapters?.length
            ? 'These chapter suggestions have no recorded timestamps.'
            : 'This draft has no chapter suggestions.'}
        </small>
      )}
    </div>
  );
}
