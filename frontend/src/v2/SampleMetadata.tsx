import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { type Report, timestamp } from './api';

const sections = {
  titles: 'Titles',
  descriptions: 'Descriptions',
  tags: 'Tags',
  chapters: 'Chapters',
};
type Section = keyof typeof sections;
type Suggestion = { text: string; reason: string; segment_index: number };
type SampleDraft = Record<Section, Suggestion[]> & { model: string };

export default function SampleMetadata({
  report,
  play,
}: {
  report: Report;
  play: (time: number) => void;
}) {
  const [draft, setDraft] = useState<SampleDraft | null>(null);
  const [section, setSection] = useState<Section>('titles');
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetch('/sample-metadata.json', { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error();
        const sample: SampleDraft = await response.json();
        if (!controller.signal.aborted) setDraft(sample);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, []);
  if (!draft)
    return (
      <p className="loading-state" role="status">
        {failed ? 'Couldn’t load the sample draft. Reload to try again.' : 'Opening sample draft…'}
      </p>
    );
  return (
    <section className="surface form-stack sample-metadata">
      <div>
        <h2>Sample metadata draft</h2>
        <p className="subtle">
          Generated from this field guide by {draft.model}. Each suggestion includes its source
          passage.
        </p>
      </div>
      <div className="tabs" role="group" aria-label="Draft category">
        {(Object.keys(sections) as Section[]).map((key) => (
          <button
            key={key}
            className={'tab' + (section === key ? ' active' : '')}
            aria-pressed={key === section}
            onClick={() => setSection(key)}
          >
            {sections[key]}
          </button>
        ))}
      </div>
      {draft[section].length === 0 && <p>No suggestions in this category.</p>}
      {draft[section].map((item, i) => {
        const passage = report.segments[item.segment_index];
        return (
          <article className="sample-suggestion" key={i}>
            <h3>{item.text}</h3>
            <p>{item.reason}</p>
            {passage && (
              <details>
                <summary>Source passage</summary>
                <blockquote>{passage.text}</blockquote>
                {passage.start_seconds != null && (
                  <button className="text-link" onClick={() => play(passage.start_seconds!)}>
                    Play at {timestamp(passage.start_seconds)}
                  </button>
                )}
              </details>
            )}
          </article>
        );
      })}
      <Link className="text-link" to="/new">
        Create a draft for your own video →
      </Link>
    </section>
  );
}
