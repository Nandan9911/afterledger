/**
 * Live voice in the browser, with the API's voice gateway played by the test: the page is told a gateway exists,
 * the microphone (a fake device playing a tone) is streamed as PCM over the WebSocket, and what the gateway says
 * back (what it heard, its spoken words, the answer) appears on the Ask screen.
 */
import { expect, test } from '@playwright/test';

test('a spoken question goes over the voice gateway and its answer is shown', async ({ page }) => {
  await page.route('**/v1/meta', async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    await route.fulfill({ response, json: { ...meta, live_gateway: '/v1/cases/{case_id}/voice' } });
  });
  let frames = 0;
  let query = '';
  await page.routeWebSocket(/\/v1\/cases\/demo-verma\/voice/, (ws) => {
    query = new URL(ws.url()).search;
    ws.send(JSON.stringify({ type: 'ready', in_rate: 16000, out_rate: 24000 }));
    ws.onMessage((message) => {
      if (typeof message === 'string') return;
      frames += 1;
      if (frames === 3) {
        ws.send(JSON.stringify({ type: 'heard', text: 'When will Narmada Bank pay?', final: true }));
        ws.send(Buffer.alloc(4800)); // 0.1 s of silence at 24 kHz
        ws.send(JSON.stringify({ type: 'said', text: 'Narmada Bank is five days late.' }));
        ws.send(JSON.stringify({ type: 'answer' }));
        ws.send(JSON.stringify({ type: 'turn_complete' }));
      }
    });
  });

  const meta = page.waitForResponse((r) => r.url().endsWith('/v1/meta'));
  await page.goto('/en/cases/demo-verma/ask');
  await meta;
  await page.locator('html[data-hydrated="true"]').waitFor();
  await page.getByRole('button', { name: 'Speak' }).first().click();
  await expect(page.getByText('Talking with AfterLedger')).toBeVisible();
  await expect(page.getByText('When will Narmada Bank pay?')).toBeVisible();
  await expect(page.getByText('Narmada Bank is five days late.')).toBeVisible();
  expect(frames).toBeGreaterThanOrEqual(3); // the microphone reached the gateway as PCM frames
  expect(query).toContain('lang=en');
});
