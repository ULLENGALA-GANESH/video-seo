import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test('a page of pending reports does not imply zero words in the last completed report', async ({ page }) => {
  await page.route('**/api/auth/me', route => route.fulfill({
    json: { id: 'creator', name: 'Studio creator', email: 'creator@example.com' },
  }));
  const items = Array.from({ length: 20 }, (_, index) => ({
    id: String(index), title: 'Pending analysis ' + index, status: 'queued', source_type: 'transcript',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    keyword_count: 0, word_count: 0,
  }));
  await page.route('**/api/analyses?limit=20', route => route.fulfill({
    json: { items, next_cursor: 'older-reports' },
  }));
  await page.goto('/');
  await expect(page.getByText('No completed report on this page')).toHaveCount(2);
  await expect(page.locator('.stat-value')).toHaveText(['20+', '—', '—']);
});

test('a visitor can trace a sample keyword to its timed source passage', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: 'Explore sample report' }).click();
  await expect(page.getByRole('heading', { name: 'A field guide to alpine photography' })).toBeVisible();

  await page.getByRole('button', { name: 'side light', exact: true }).click();
  await expect(page.getByRole('blockquote')).toContainText('side light is the first thing I look for');
  await page.getByRole('button', { name: 'Play at 0:10' }).click();
  const video = page.getByLabel('A field guide to alpine photography, narrated sample');
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBeGreaterThanOrEqual(10);
  const sample = await page.request.get('/sample-report.json');
  expect(sample.ok()).toBeTruthy();
  const { duration_seconds: declaredDuration } = await sample.json();
  const mediaDuration = await video.evaluate((element: HTMLVideoElement) => element.duration);
  expect(Math.abs(mediaDuration - declaredDuration)).toBeLessThanOrEqual(0.15);

  await page.getByRole('button', { name: 'Transcript', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Transcript', exact: true })).toBeVisible();
  await expect(page.getByText(/Side light catches the limestone edges/)).toBeVisible();

  await page.getByRole('button', { name: 'AI metadata', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Sample metadata draft' })).toBeVisible();
  await page.getByRole('button', { name: 'Chapters', exact: true }).click();
  await page.getByText('Source passage', { exact: true }).first().click();
  await expect(page.locator('.sample-suggestion blockquote').first()).toBeVisible();
});

test('a creator can analyze a transcript, reopen the report, and save a profile', async ({ page }) => {
  const email = `creator-${randomUUID()}@example.com`;
  const password = randomUUID();
  const title = 'Kitchen garden notes';
  const passage = 'Basil roots thrive in warm soil.';
  const transcript = passage + ' Keep basil roots damp with morning water. Warm soil helps basil roots grow steadily.';

  await page.goto('/signup');
  await page.getByLabel('Display name').fill('Garden editor');
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Create account' }).click();
  await expect(page.getByRole('heading', { name: /Workspace overview/ })).toBeVisible();

  const navigation = page.getByRole('navigation', { name: 'Studio navigation' });
  await navigation.getByRole('link', { name: 'New analysis' }).click();
  await page.getByLabel('Report title').fill(title);
  await page.getByRole('textbox', { name: /^Transcript/ }).fill(transcript);
  await page.getByRole('button', { name: 'Start analysis' }).click();
  await expect(page).toHaveURL(/\/analyses\/[a-f0-9]{24}$/);
  const reportURL = page.url();
  await expect(page.getByRole('heading', { name: 'Keyword evidence' })).toBeVisible();
  await page.getByRole('button', { name: 'basil roots', exact: true }).click();
  await expect(page.getByRole('blockquote')).toHaveText(passage);
  await page.getByRole('button', { name: 'Read in transcript' }).click();
  await expect(page.getByRole('heading', { name: 'Transcript', exact: true })).toBeVisible();

  const writes: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      writes.push(request.method() + ' ' + path);
    }
  });
  await page.reload();
  await expect(page).toHaveURL(reportURL);
  await expect(page.getByRole('heading', { name: title })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Keyword evidence' })).toBeVisible();
  await navigation.getByRole('link', { name: 'Analysis library' }).click();
  await expect(page.getByRole('link', { name: title })).toHaveCount(1);
  expect(writes).toEqual([]);

  await navigation.getByRole('link', { name: 'Profile' }).click();
  await page.getByLabel('Display name').fill('Updated garden editor');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByRole('status')).toContainText('Your profile has been saved.');
  await page.reload();
  await expect(page.getByLabel('Display name')).toHaveValue('Updated garden editor');
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: /Workspace overview/ })).toBeVisible();
  await page.goto(reportURL);
  await expect(page).toHaveURL(/\/login\?next=/);
  await expect(page.getByRole('heading', { name: /Welcome back/ })).toBeVisible();
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page).toHaveURL(reportURL);
  await expect(page.getByRole('heading', { name: title })).toBeVisible();
});
