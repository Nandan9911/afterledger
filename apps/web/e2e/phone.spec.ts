/**
 * The app on a phone (390 × 844, touch): no page scrolls sideways, in English or in right-to-left Arabic; the main
 * screens pass axe; the tabs and the More sheet reach every screen; and what a family does from a phone works end to
 * end: telling the story and changing the summary, approving papers and marking them handed in, sharing the claim
 * with the institution's desk, and putting the demo case back as it was.
 */
import { expect, type Page, test } from '@playwright/test';
import { accessible, demosReady, hydrated } from './helpers';

const API = 'http://localhost:8080';

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

test.beforeAll(async ({ request }) => {
  await demosReady(request);
});

/** How far the page reaches past the screen's edge. On a phone the window grows with whatever overflows, so the
 * page is measured against the screen (the root's client width), not the window. */
async function sideways(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

test('no page scrolls sideways on a phone, left to right or right to left', async ({ page, request }) => {
  test.setTimeout(400_000);
  const plans = await (await request.get(`${API}/v1/cases/demo-verma/plans`)).json();
  const packs = await (await request.get(`${API}/v1/cases/demo-verma/packs`)).json();
  const screens = ['today', 'story', 'papers', 'money', 'leads', 'people', 'plans', 'packs', 'tracker', 'sources', 'activity', 'ask'];
  const pages = [
    '',
    '/new',
    '/challenge',
    '/evaluation',
    ...screens.map((s) => `/cases/demo-verma/${s}`),
    `/cases/demo-verma/plans/${plans.rows.find((r: { plan: unknown }) => r.plan).plan.id}`,
    `/cases/demo-verma/packs/${packs.rows[0].pack.id}`,
  ];
  const wide: string[] = [];
  for (const locale of ['en', 'ar']) {
    for (const path of pages) {
      await page.goto(`/${locale}${path}`);
      await hydrated(page);
      await expect(page.locator('main, #main').first()).not.toBeEmpty();
      await page.waitForTimeout(300); // the case's data arrives just after the page answers
      const over = await sideways(page);
      if (over > 1) wide.push(`/${locale}${path}: ${over}px`);
    }
  }
  expect(wide).toEqual([]);
});

test('the main screens pass axe on a phone', async ({ page, request }) => {
  test.setTimeout(180_000);
  const plans = await (await request.get(`${API}/v1/cases/demo-verma/plans`)).json();
  for (const path of [
    '/en',
    '/en/cases/demo-verma/today',
    '/en/cases/demo-verma/papers',
    `/en/cases/demo-verma/plans/${plans.rows.find((r: { plan: unknown }) => r.plan).plan.id}`,
    '/en/cases/demo-verma/tracker',
    '/en/cases/demo-verma/people',
    '/en/evaluation',
    '/ar/cases/demo-clarke/money',
  ]) {
    await page.goto(path);
    await hydrated(page);
    await expect(page.locator('main, #main').first()).not.toBeEmpty();
    await accessible(page);
  }
});

test('the tabs and the More sheet reach every screen of a case', async ({ page }) => {
  await page.goto('/en/cases/demo-verma/today');
  await hydrated(page);
  const tabs = page.getByRole('navigation', { name: 'Main' });
  await tabs.getByRole('link', { name: 'Papers' }).click();
  await expect(page).toHaveURL(/\/cases\/demo-verma\/papers$/);
  await tabs.getByRole('button', { name: 'More' }).click();
  await page.getByRole('link', { name: 'Sources' }).click();
  await expect(page).toHaveURL(/\/cases\/demo-verma\/sources$/);
  await expect(page.getByRole('link', { name: 'Sources' })).toBeHidden(); // the sheet closed on the way
});

test('on a phone, a family tells the story, changes the summary, then confirms it', async ({ page, request }) => {
  const started = await (await request.post(`${API}/v1/cases`, { data: { lang: 'en', starter_name: 'Karim' } })).json();
  const id = started.case.id;
  for (const text of [
    'My mother died last month in Lyon.',
    'Marie Dubois',
    'On 12 September 2026, in Lyon, France.',
    'She had an account at Crédit Rhône Bank.',
    'My brother Paul lives in Paris.',
    'No will.',
  ]) {
    await request.post(`${API}/v1/cases/${id}/turns`, { data: { text } });
  }
  await page.goto(`/en/cases/${id}/story`);
  await hydrated(page);
  const known = page.getByRole('complementary', { name: 'What we know so far' });
  await expect(known.getByText('Is this right?')).toBeVisible();
  await expect(known).not.toContainText('Claire');

  await known.getByRole('button', { name: 'Change something' }).click();
  await known.getByLabel('What should we change?').fill('My sister Claire Dubois lives in Nice.');
  await known.getByRole('button', { name: 'Send the change' }).click();
  await expect(page.getByText('Thank you. We will correct it.')).toBeVisible();
  // the change is heard like anything else said, and the summary comes back rebuilt from it
  await expect(known).toContainText('Claire');
  await known.getByRole('button', { name: 'Yes, this is right' }).click();
  await expect(page.getByRole('link', { name: 'Add the papers' })).toBeVisible();

  // the home page lists the family's own cases, so the case can be found again, and deleted after asking
  await page.goto('/en');
  await hydrated(page);
  const mine = page.getByRole('region', { name: 'Your cases' }).getByRole('listitem').filter({ hasText: 'Marie Dubois' });
  await expect(mine.getByRole('link', { name: 'Open case' })).toHaveAttribute('href', `/en/cases/${id}/today`);
  await mine.getByRole('button', { name: 'Delete this case' }).click();
  await mine.getByRole('button', { name: 'Delete it' }).click();
  await expect(page.getByText('The case was deleted.')).toBeVisible();
  expect((await request.get(`${API}/v1/cases/${id}`)).status()).toBe(404);
});

test('on a phone, papers are approved, marked handed in and shared with the desk; the demo case is then reset', async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const packs = await (await request.get(`${API}/v1/cases/demo-verma/packs`)).json();
  const row = packs.rows.find((r: { pack: { status: string } }) => r.pack.status === 'ready');
  await page.goto(`/en/cases/demo-verma/packs/${row.pack.id}`);
  await hydrated(page);
  await page.getByRole('button', { name: 'Approve these papers' }).click();
  await page.getByRole('button', { name: 'Mark as handed in' }).click();
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText("Recorded. The tracker now follows the institution's time limits.")).toBeVisible();
  await expect(page.getByText(/^Handed in on /)).toBeVisible();

  await page.getByRole('link', { name: 'Tracker' }).click();
  await expect(page).toHaveURL(/\/tracker$/);
  await page.getByRole('button', { name: /^Share with Awadh Securities/ }).click();
  const desk = page.getByRole('link', { name: 'Open the desk page' });
  await expect(desk).toBeVisible();
  const href = await desk.getAttribute('href');
  await page.goto(href!);
  await hydrated(page);
  await expect(page.getByRole('heading', { level: 1 })).toContainText('The claim of the late');
  expect(await sideways(page)).toBeLessThanOrEqual(1);
  await accessible(page);

  await page.goto('/en/cases/demo-verma/today');
  await hydrated(page);
  await page.getByRole('navigation', { name: 'Main' }).getByRole('button', { name: 'More' }).click();
  await page.getByRole('button', { name: 'Reset this sample case' }).click();
  await page.getByRole('button', { name: 'Reset it' }).click();
  await expect(page.getByText('The sample case is back as it was.')).toBeVisible();
  const after = await (await request.get(`${API}/v1/cases/demo-verma/packs/${row.pack.id}`)).json();
  expect(after.pack.status).toBe('ready');
  expect(after.claim.submitted_on).toBeNull();
});
