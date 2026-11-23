import { expect, test, type Page } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';

async function privateReport(page: Page, transcript: string) {
  const account = await page.request.post('/api/auth/signup', {
    data: {
      name: 'Report editor',
      email: `editor-${randomUUID()}@example.com`,
      password: randomUUID(),
    },
  });
  expect(account.status()).toBe(201);
  const queued = await page.request.post('/api/analyses', {
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
    multipart: { title: 'Editorial review', transcript },
  });
  expect(queued.status()).toBe(202);
  const { id } = await queued.json();
  await page.goto('/analyses/' + id);
  await expect(page.getByRole('heading', { name: 'Keyword evidence' })).toBeVisible();
  return id;
}

async function correctTranscript(page: Page, value: string) {
  await page.getByRole('button', { name: 'Transcript', exact: true }).click();
  await page.getByRole('button', { name: 'Correct transcript' }).click();
  await page.getByRole('textbox', { name: 'Transcript text' }).fill(value);
  const saved = page.waitForResponse(
    (response) => response.url().endsWith('/transcript') && response.request().method() === 'PUT',
  );
  await page.getByRole('button', { name: 'Save and reanalyze' }).click();
  expect((await saved).status()).toBe(200);
  await expect(page.getByRole('heading', { name: 'Keyword evidence' })).toBeVisible();
}

test('corrected passages rerank the same private report and expose its metadata editor', async ({
  page,
}) => {
  const id = await privateReport(page, 'Basil roots grow early. Basil roots favor warmth.');
  const updated = 'Lavender blooms support pollinators. Lavender blooms emerge in summer.';
  await correctTranscript(page, updated);
  await expect(page).toHaveURL(new RegExp('/analyses/' + id + '$'));
  await page.getByRole('button', { name: 'lavender blooms', exact: true }).click();
  await expect(page.getByRole('blockquote')).toHaveText('Lavender blooms support pollinators.');
  await expect(page.getByRole('button', { name: 'basil roots', exact: true })).toHaveCount(0);
  await page.reload();
  await page.getByRole('button', { name: 'AI metadata' }).click();
  await expect(page.getByRole('button', { name: 'Generate metadata draft' })).toBeEnabled();
  const persisted = await (await page.request.get('/api/analyses/' + id)).json();
  expect(persisted.transcript).toBe(updated);
  expect(persisted.transcript_revision).toBe(1);
});

test('local Ollama draft can be edited, exported, and reviewed after a correction', async ({
  page,
}) => {
  test.skip(process.env.E2E_OLLAMA_LIVE !== '1', 'Requires an installed local Ollama model.');
  test.setTimeout(600_000);
  const sample = await (await page.request.get('/sample-report.json')).json();
  const id = await privateReport(page, sample.transcript);
  await page.getByRole('button', { name: 'AI metadata' }).click();
  const generated = page.waitForResponse(
    (response) => response.url().endsWith('/metadata/generate'),
    { timeout: 510_000 },
  );
  await page.getByRole('button', { name: 'Generate metadata draft' }).click();
  const response = await generated;
  expect(response.status(), response.ok() ? undefined : await response.text()).toBe(200);
  await expect(page.getByText('Draft generated and saved.', { exact: true })).toBeVisible();
  const firstTitle = page.getByRole('textbox', { name: 'Suggestion' }).first();
  await firstTitle.fill('Photographing alpine light');
  await page.getByRole('button', { name: 'Save draft edits' }).click();
  await expect(page.getByText('Draft changes saved.', { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'AI metadata' }).click();
  await expect(firstTitle).toHaveValue('Photographing alpine light');
  await expect(page.getByRole('link', { name: 'Prepare YouTube upload' })).toHaveAttribute(
    'href',
    '/channel?report=' + id,
  );
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.getByRole('button', { name: 'Copy metadata' }).click();
  await expect(page.getByText('Metadata copied.', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain(
    'Photographing alpine light',
  );
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download saved JSON' }).click();
  const file = await (await downloaded).path();
  const exported = JSON.parse(await readFile(file!, 'utf8'));
  expect(exported.report_id).toBe(id);
  expect(exported.draft.titles[0].text).toBe('Photographing alpine light');
  expect(exported.draft.evidence[exported.draft.titles[0].segment_index].text).toBeTruthy();
  await correctTranscript(page, sample.transcript + ' Observe the final exposure.');
  await page.getByRole('button', { name: 'AI metadata' }).click();
  await expect(page.getByText(/The transcript has changed/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Copy metadata' })).toBeDisabled();
  await expect(page.getByRole('link', { name: 'Prepare YouTube upload' })).toHaveCount(0);
});
