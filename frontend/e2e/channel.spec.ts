import { expect, test } from '@playwright/test';

test('connected channel requires explicit upload and metadata confirmation', async ({
  page,
}) => {
  const info = {
    id: 'channel-fixture',
    title: 'Test channel',
    views: 123,
    subscribers: 12,
    videos: 1,
  };
  const video = {
    id: 'video-fixture',
    title: 'Original title',
    description: '',
    tags: [],
    visibility: 'private',
    etag: 'original',
  };
  let starts = 0,
    chunks = 0,
    updates = 0;
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: { id: 'test-user', name: 'Creator', email: 'creator@example.test' },
    }),
  );
  await page.route('**/api/channel**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/channel')
      return route.fulfill({ json: { configured: true, channel: info } });
    if (path.endsWith('/analytics'))
      return route.fulfill({
        json: {
          channel: info,
          start: '2026-01-01',
          end: '2026-01-28',
          rows: [
            {
              day: '2026-01-01',
              views: 8,
              estimatedMinutesWatched: 4,
              subscribersGained: 1,
              subscribersLost: 0,
            },
          ],
        },
      });
    if (path.endsWith('/videos'))
      return route.fulfill({ json: { items: [video], next: null } });
    if (path.endsWith('/videos/video-fixture')) {
      const data = route.request().postDataJSON();
      expect(data.confirmed).toBe(true);
      updates++;
      return route.fulfill({ json: { ...video, ...data, etag: 'updated' } });
    }
    if (path.endsWith('/uploads')) {
      const data = route.request().postDataJSON();
      expect(data.visibility).toBe('private');
      expect(data.confirmed).toBe(true);
      starts++;
      return route.fulfill({
        json: { id: 'transfer', offset: 0, size: 3, video_id: null },
      });
    }
    if (path.endsWith('/uploads/transfer')) {
      expect(route.request().headers()['content-range']).toBe('bytes 0-2/3');
      chunks++;
      return route.fulfill({
        json: {
          id: 'transfer',
          offset: 3,
          size: 3,
          video_id: 'uploaded',
          visibility: 'private',
        },
      });
    }
    throw new Error('Unexpected channel request: ' + path);
  });
  await page.goto('/channel');
  await expect(
    page.getByRole('heading', { name: 'Test channel' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Load 28-day analytics' }).click();
  await expect(
    page.getByRole('table', { name: 'Daily channel activity' }),
  ).toBeVisible();
  const upload = page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name: 'Upload to YouTube' }) })
    .last();
  await upload
    .getByLabel('Source video')
    .setInputFiles({
      name: 'clip.mp4',
      mimeType: 'video/mp4',
      buffer: Buffer.from('abc'),
    });
  await upload.getByLabel('Video title').fill('Reviewed upload');
  await upload.getByLabel('Is this video made for kids?').selectOption('no');
  await expect(
    upload.getByRole('button', { name: 'Upload video' }),
  ).toBeDisabled();
  expect(starts).toBe(0);
  await upload.getByRole('checkbox').check();
  await upload.getByRole('button', { name: 'Upload video' }).click();
  await expect(upload.getByRole('link', { name: 'View video' })).toBeVisible();
  expect([starts, chunks]).toEqual([1, 1]);
  await page.getByRole('button', { name: 'Load channel videos' }).click();
  await page.getByRole('button', { name: 'Edit metadata' }).click();
  const form = page
    .locator('form')
    .filter({
      has: page.getByRole('heading', { name: 'Edit YouTube metadata' }),
    });
  await form.getByLabel('Video title').fill('Reviewed edit');
  await expect(
    form.getByRole('button', { name: 'Save on YouTube' }),
  ).toBeDisabled();
  await form.getByRole('checkbox').check();
  await form.getByRole('button', { name: 'Save on YouTube' }).click();
  await expect(page.getByText('Metadata saved on YouTube.')).toBeVisible();
  expect(updates).toBe(1);
});

test('publishing handoff offers alternatives and appends only recorded chapter times', async ({
  page,
}) => {
  const id = '000000000000000000000001';
  let timed = true;
  let submission: Record<string, unknown> | undefined;
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: { id: 'owner', name: 'Creator', email: 'owner@example.test' },
    }),
  );
  await page.route('**/api/channel', (route) =>
    route.fulfill({
      json: {
        configured: true,
        channel: {
          id: 'mine',
          title: 'Test channel',
          views: 0,
          subscribers: 0,
          videos: 0,
        },
      },
    }),
  );
  await page.route('**/api/analyses/*/metadata', (route) =>
    route.fulfill({
      json: {
        revision: 3,
        transcript_revision: 2,
        stale: false,
        draft: {
          titles: [{ text: 'First title' }, { text: 'Second title' }],
          descriptions: [
            { text: 'Initial copy.' },
            { text: 'Alternate copy.' },
          ],
          tags: [{ text: 'cinematography' }],
          chapters: [
            { text: 'Lighting', segment_index: 2 },
            { text: 'Opening', segment_index: 0 },
            { text: 'Duplicate start', segment_index: 1 },
            { text: 'Untimed', segment_index: 3 },
          ],
          evidence: {
            0: { start_seconds: timed ? 0 : null },
            1: { start_seconds: timed ? 0.4 : null },
            2: { start_seconds: timed ? 15.8 : null },
            3: { start_seconds: null },
          },
        },
      },
    }),
  );
  await page.route('**/api/channel/uploads', (route) => {
    submission = route.request().postDataJSON();
    return route.fulfill({
      json: { id: 'fixture', offset: 0, size: 3, video_id: null },
    });
  });
  await page.route('**/api/channel/uploads/fixture', (route) =>
    route.fulfill({
      json: {
        id: 'fixture',
        offset: 3,
        size: 3,
        video_id: 'fixture',
        visibility: 'private',
      },
    }),
  );
  await page.goto('/channel?report=' + id);
  await page.getByLabel('Title suggestion').selectOption('Second title');
  await expect(page.getByLabel('Video title', { exact: true })).toHaveValue(
    'Second title',
  );
  const description = page.getByRole('textbox', { name: /^Description\b/ });
  const add = page.getByRole('button', { name: 'Add chapter timestamps' });
  await description.fill('á'.repeat(2498));
  await expect(add).toBeDisabled();
  await description.fill('Initial copy.\n\n0:15 My lighting note');
  await page.getByText('Review chapter timestamps (2)').click();
  await expect(page.getByRole('time').first()).toHaveText('00:00');
  await add.click();
  await expect(description).toHaveValue(
    'Initial copy.\n\n00:00 Opening\n0:15 My lighting note',
  );
  await expect(add).toBeDisabled();
  await page
    .getByLabel('Description suggestion')
    .selectOption('Alternate copy.');
  const final = 'Alternate copy.\n\n00:00 Opening\n0:15 My lighting note';
  await expect(description).toHaveValue(final);
  await page
    .getByLabel('Source video')
    .setInputFiles({
      name: 'clip.mp4',
      mimeType: 'video/mp4',
      buffer: Buffer.from('abc'),
    });
  await page.getByLabel('Is this video made for kids?').selectOption('no');
  const confirm = page.getByLabel(/Upload this video to my channel/);
  await confirm.check();
  await page.getByLabel('Title suggestion').selectOption('First title');
  await expect(confirm).not.toBeChecked();
  expect(submission).toBeUndefined();
  await confirm.check();
  await page.getByRole('button', { name: 'Upload video' }).click();
  await expect(page.getByRole('link', { name: 'View video' })).toBeVisible();
  expect(submission).toMatchObject({
    title: 'First title',
    description: final,
    report_id: id,
    metadata_revision: 3,
    transcript_revision: 2,
    confirmed: true,
  });
  timed = false;
  await page.reload();
  await expect(
    page.getByText('These chapter suggestions have no recorded timestamps.'),
  ).toBeVisible();
  await expect(add).toHaveCount(0);
});
