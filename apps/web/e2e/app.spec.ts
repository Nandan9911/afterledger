/**
 * The app in a browser, offline: the home page, a demo case's screens in three languages, a question answered from
 * the case, the live challenge, and the Evaluation page. Every page is checked with axe for serious accessibility
 * problems.
 */
import { expect, test } from '@playwright/test';
import { accessible, hydrated } from './helpers';

test.beforeAll(async ({ request }) => {
  // the demo cases are restored from their bundles when the API starts
  await expect
    .poll(
      async () => (await (await request.get('http://localhost:8080/v1/demo/families')).json()).every((f: { seeded: boolean }) => f.seeded),
      {
        timeout: 120_000,
      },
    )
    .toBe(true);
});

test('the home page lists the six demo families', async ({ page }) => {
  await page.goto('/en');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Find everything they left behind');
  await expect(page.locator('#families').getByRole('link', { name: /Open case/ })).toHaveCount(6);
  await accessible(page);
});

test("a demo case's Today, in English and in the family's Hindi", async ({ page }) => {
  await page.goto('/en/cases/demo-verma/today');
  await expect(page.getByText('Good morning, Amit')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Look for the locker key' })).toBeVisible();
  await accessible(page);

  await page.goto('/hi/cases/demo-verma/today');
  await expect(page.locator('html')).toHaveAttribute('lang', 'hi');
  await expect(page.getByText('सुप्रभात, अमित')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'लॉकर की चाबी ढूँढें' })).toBeVisible();
});

test('Arabic pages read right to left', async ({ page }) => {
  await page.goto('/ar');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await accessible(page);
});

test('a paper opens with its page image and the values read from it', async ({ page }) => {
  await page.goto('/en/cases/demo-verma/papers');
  await hydrated(page);
  await page
    .getByRole('list', { name: 'All papers' })
    .getByRole('button', { name: /Sahyadri Bank statement/ })
    .click();
  const viewer = page.getByRole('region', { name: 'Page viewer' });
  await expect(viewer).toContainText('Sahyadri Bank statement, page 1');
  const image = viewer.locator('img[src*="/pages/"]').first();
  await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  await expect(page.getByRole('heading', { name: 'Read from this page' })).toBeVisible();
  await accessible(page);
});

test('the late claim shows what the bank owes for the delay', async ({ page }) => {
  await page.goto('/en/cases/demo-verma/plans');
  await hydrated(page);
  await page
    .getByRole('link')
    .filter({ hasText: /Narmada/ })
    .first()
    .click();
  await expect(page.getByText('₹835.31').first()).toBeVisible();
  await expect(page.getByText('₹167.06').first()).toBeVisible();
  await accessible(page);

  // every rule opens in its source's own words, and closing it returns to where the reader was
  const why = page.locator('button:not([aria-pressed])', { hasText: 'Why?' }).first();
  await why.click();
  const panel = page.getByRole('dialog');
  await expect(panel).toBeVisible();
  await expect(panel.locator('blockquote').first()).toBeVisible();
  await expect(panel.getByRole('link', { name: /Open the page/ }).first()).toHaveAttribute('href', /^https:\/\//);
  await page.keyboard.press('Escape');
  await expect(panel).toHaveCount(0);
  await expect(why).toBeFocused();
});

test('a typed question is answered from the case, with its source', async ({ page }) => {
  await page.goto('/en/cases/demo-verma/ask');
  await hydrated(page);
  await page.getByRole('button', { name: 'Type' }).click();
  await page.getByPlaceholder('Ask anything about the case').fill('When will Narmada Bank pay?');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByText('When will Narmada Bank pay?').last()).toBeVisible();
  await expect(page.getByText(/See the source|Official source|From the case/).last()).toBeVisible();
  await accessible(page);
});

test('the live challenge researches France and checks every quote', async ({ page }) => {
  await page.goto('/en/challenge');
  await hydrated(page);
  await page.getByRole('button').filter({ hasText: 'France' }).first().click();
  await expect(page).toHaveURL(/\/challenge\/ch_/);
  await expect(page.getByText(/Matches the hand-checked gold plan/)).toBeVisible({ timeout: 60_000 });
  await page.getByRole('button', { name: 'Open every check' }).click();
  await expect(page.getByText(/Dropped: not on its page/).first()).toBeVisible();
  await accessible(page);
});

test('the Evaluation page says what this copy runs on', async ({ page }) => {
  await page.goto('/en/evaluation');
  await expect(page.getByRole('heading', { name: 'What this copy runs on' })).toBeVisible();
  await expect(page.getByText('Scripted responses, offline')).toBeVisible();
  await expect(page.getByText(/Starting values set by hand|Measured on/)).toBeVisible();
  await expect(page.getByText("The browser's own speech recognition and voice")).toBeVisible();
  await accessible(page);
});

test('every screen of every demo case renders without a browser error', async ({ page, request }) => {
  test.setTimeout(600_000);
  const errors: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`${page.url()}: ${m.text().slice(0, 300)}`);
  });
  page.on('pageerror', (e) => errors.push(`${page.url()}: ${e.message.slice(0, 300)}`));
  const screens = ['today', 'story', 'papers', 'money', 'leads', 'people', 'plans', 'packs', 'tracker', 'sources', 'activity', 'ask'];
  const families: Array<{ key: string }> = await (await request.get('http://localhost:8080/v1/demo/families')).json();
  for (const { key } of families) {
    for (const screen of screens) {
      await page.goto(`/en/cases/demo-${key}/${screen}`);
      await hydrated(page);
      await expect(page.locator('main')).not.toBeEmpty();
    }
    const plans = await (await request.get(`http://localhost:8080/v1/cases/demo-${key}/plans`)).json();
    for (const row of plans.rows.filter((r: { plan: unknown }) => r.plan).slice(0, 2)) {
      await page.goto(`/en/cases/demo-${key}/plans/${row.plan.id}`);
      await hydrated(page);
    }
    const packs = await (await request.get(`http://localhost:8080/v1/cases/demo-${key}/packs`)).json();
    for (const row of packs.rows.slice(0, 2)) {
      await page.goto(`/en/cases/demo-${key}/packs/${row.pack.id}`);
      await hydrated(page);
    }
  }
  expect(errors).toEqual([]);
});

test('the screens in the other five languages render without a browser error', async ({ page }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`${page.url()}: ${m.text().slice(0, 300)}`);
  });
  page.on('pageerror', (e) => errors.push(`${page.url()}: ${e.message.slice(0, 300)}`));
  const visits: Array<[string, string]> = [
    ['hi', 'verma'],
    ['ja', 'sato'],
    ['es', 'morales'],
    ['ml', 'nair'],
    ['ar', 'clarke'],
  ];
  for (const [locale, key] of visits) {
    for (const screen of ['today', 'papers', 'money', 'plans', 'tracker', 'ask']) {
      await page.goto(`/${locale}/cases/demo-${key}/${screen}`);
      await hydrated(page);
      await expect(page.locator('html')).toHaveAttribute('lang', locale);
    }
  }
  expect(errors).toEqual([]);
});
