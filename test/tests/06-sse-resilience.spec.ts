import { expect, test } from '@playwright/test';
import { openApp, tid } from './helpers';

declare global {
  interface Window { __eventSources: EventSource[] }
}

test.describe('SSE resilience', () => {
  test('status leaves "connected" when the stream drops and returns when restored', async ({ page }) => {
    // Chromium's offline emulation does not tear down an already-open EventSource,
    // so record every EventSource the app creates in order to drop it from the page side.
    await page.addInitScript(() => {
      const Native = window.EventSource;
      window.__eventSources = [];
      class Tracked extends Native {
        constructor(url: string | URL, init?: EventSourceInit) {
          super(url, init);
          window.__eventSources.push(this);
        }
      }
      window.EventSource = Tracked as typeof EventSource;
    });

    await openApp(page);
    const status = tid(page, 'connection-status');
    await expect(status).toHaveAttribute('data-status', 'connected');

    // Server becomes unreachable: every reconnect attempt fails at the network level.
    await page.route('**/api/stream/prices', (route) => route.abort('internetdisconnected'));
    // Drop the live connection (the browser closes it and reports an error).
    await page.evaluate(() => {
      for (const es of window.__eventSources) {
        if (es.readyState !== EventSource.CLOSED) {
          es.close();
          es.dispatchEvent(new Event('error'));
        }
      }
    });
    await expect(status).toHaveAttribute('data-status', /^(reconnecting|disconnected)$/);

    // While it's down, the app keeps trying (new EventSource attempts are made).
    const attempts = await page.evaluate(() => window.__eventSources.length);
    await expect.poll(() => page.evaluate(() => window.__eventSources.length), { timeout: 15_000 }).toBeGreaterThan(attempts);
    await expect(status).not.toHaveAttribute('data-status', 'connected');

    // Server is back: the app reconnects by itself.
    await page.unroute('**/api/stream/prices');
    await expect(status).toHaveAttribute('data-status', 'connected', { timeout: 30_000 });

    // And prices keep flowing after the reconnect.
    const price = tid(page, 'price-AAPL');
    const before = (await price.textContent())!.trim();
    await expect(price).not.toHaveText(before, { timeout: 15_000 });
  });
});
