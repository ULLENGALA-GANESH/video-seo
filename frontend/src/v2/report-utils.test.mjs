import { test } from 'node:test';
import assert from 'node:assert/strict';
import { evidenceSegmentIndex, filenameFor, keywordCsv } from './report-utils.ts';

test('CSV preserves cell boundaries, embedded quotes, a newline and zero seconds', () => {
  const csv = keywordCsv([
    {
      phrase: 'blue, hour',
      score: 100,
      occurrences: 2,
      evidence: 'The "blue"\nhour.',
      start_seconds: 0,
    },
  ]);
  assert.ok(csv.startsWith('\uFEFF'));
  assert.ok(csv.endsWith('"blue, hour","100","2","The ""blue""\nhour.","0"'));
});

test('spreadsheet formula prefixes are neutralized and missing timestamps stay empty', () => {
  const csv = keywordCsv([
    { phrase: '=term', score: 40, occurrences: 1, evidence: '  +value', start_seconds: null },
  ]);
  assert.ok(csv.endsWith(`"'=term","40","1","'  +value",""`));
});

test('attachment names stay portable and retain non-Latin titles', () => {
  assert.equal(filenameFor('  /field: notes?/  '), 'video-seo-field-notes.csv');
  assert.equal(filenameFor('山'), 'video-seo-山.csv');
  assert.equal(filenameFor('...'), 'video-seo-report.csv');
});

test('transcript navigation prefers the cited passage over an earlier partial match', () => {
  const segments = [
    { text: 'Catering tips.', start_seconds: 0 },
    { text: 'The cat sleeps.', start_seconds: 4 },
    { text: 'The cat sleeps.', start_seconds: 9 },
  ];
  const keyword = { phrase: 'cat', evidence: 'The cat sleeps.', start_seconds: 9 };
  assert.equal(evidenceSegmentIndex(segments, keyword), 2);
  assert.equal(evidenceSegmentIndex(segments, { ...keyword, start_seconds: null }), 1);
});
