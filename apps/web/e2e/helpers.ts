/** What the browser tests share: waiting for the page to answer clicks, and the axe check. */
import AxeBuilder from '@axe-core/playwright';
import { type APIRequestContext, expect, type Page } from '@playwright/test';

/** Waits until React has taken over the server-rendered page, so a click is answered. */
export async function hydrated(page: Page) {
  await page.locator('html[data-hydrated="true"]').waitFor();
}

export async function accessible(page: Page) {
  // let entrance animations finish first: axe measures contrast as drawn, and the home page's callout fades in
  // over its first two seconds (a warm server answers before then)
  await page.waitForFunction(() =>
    document.getAnimations().every((a) => a.playState !== 'running' || a.effect?.getComputedTiming().endTime === Infinity),
  );
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  expect(
    serious.map(
      (v) =>
        `${v.id}: ${v.nodes
          .map((n) => n.target.join(' '))
          .slice(0, 3)
          .join(', ')}`,
    ),
  ).toEqual([]);
}

/** The demo cases are restored from their bundles when the API starts. */
export async function demosReady(request: APIRequestContext) {
  await expect
    .poll(
      async () => (await (await request.get('http://localhost:8080/v1/demo/families')).json()).every((f: { seeded: boolean }) => f.seeded),
      { timeout: 120_000 },
    )
    .toBe(true);
}
