import type { Keyword, Segment } from './api';

export function evidenceSegmentIndex(segments: Segment[], keyword: Keyword) {
  const exact = segments.findIndex(segment =>
    keyword.evidence && segment.text.includes(keyword.evidence) &&
    (keyword.start_seconds == null || segment.start_seconds === keyword.start_seconds)
  );
  if (exact >= 0) return exact;

  // Older reports can lack matching timestamps or an excerpt.
  const excerpt = segments.findIndex(segment => keyword.evidence && segment.text.includes(keyword.evidence));
  if (excerpt >= 0) return excerpt;
  const phrase = keyword.phrase.toLowerCase();
  return segments.findIndex(segment => segment.text.toLowerCase().includes(phrase));
}

function csvCell(value: string | number) {
  let text = String(value);
  if (typeof value === 'string' && /^[\s\uFEFF]*[=+@-]/.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}

export function keywordCsv(keywords: Keyword[]) {
  const rows: (string | number)[][] = [
    ['Keyword', 'Relevance (0–100)', 'Occurrences', 'Transcript evidence', 'Start (seconds)'],
    ...keywords.map((item) => [
      item.phrase,
      item.score,
      item.occurrences,
      item.evidence,
      item.start_seconds ?? '',
    ]),
  ];
  return '\uFEFF' + rows.map((row) => row.map(csvCell).join(',')).join('\r\n');
}

export function filenameFor(title: string) {
  const stem = title
    .normalize('NFKD')
    .replace(/[^\p{Letter}\p{Number}]+/gu, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
  return 'video-seo-' + (stem || 'report') + '.csv';
}
