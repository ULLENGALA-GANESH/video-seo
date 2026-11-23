import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test('a creator can save a keyword assessment and revisit a completion notice', async ({
  page,
}) => {
  const account = await page.request.post('/api/auth/signup', {
    data: {
      name: 'Research editor',
      email: `research-${randomUUID()}@example.com`,
      password: randomUUID(),
    },
  });
  expect(account.status()).toBe(201);

  await page.goto('/new');
  await page.getByLabel('Report title').fill('Winter trail guide');
  await page
    .getByRole('textbox', { name: /^Transcript/ })
    .fill(
      'Winter trails need careful planning. Walk winter trails with warm boots. Winter trails offer beautiful views.',
    );
  await page.getByRole('button', { name: 'Start analysis' }).click();
  await page.getByRole('button', { name: 'Research on YouTube' }).click();
  await page.getByRole('combobox', { name: 'Competition', exact: true }).selectOption('moderate');
  await page
    .getByLabel('Reasoning and sources')
    .fill('The audience is specific to winter walking.');
  await page.getByRole('button', { name: 'Save assessment' }).click();
  await expect(page.getByText('Assessment saved.', { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'YouTube research', exact: true }).click();
  await expect(page.getByRole('combobox', { name: 'Competition', exact: true })).toHaveValue(
    'moderate',
  );

  await page.getByRole('link', { name: 'Notifications', exact: true }).click();
  await expect(page.getByText('Winter trail guide')).toBeVisible();
  await page.getByRole('button', { name: 'Mark read' }).click();
  await expect(page.getByRole('button', { name: 'Mark read' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Refresh' }).click();
  await expect(page.getByRole('link', { name: 'Open report' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Mark read' })).toHaveCount(0);

  await page.getByRole('link', { name: 'YouTube channel', exact: true }).click();
  await expect(page.getByRole('heading', { name: /YouTube channel/i }).first()).toBeVisible();
  await expect(page.getByText(/YouTube connection is unavailable/)).toBeVisible();
});
