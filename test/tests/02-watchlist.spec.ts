import { expect, test } from '@playwright/test';
import { closePosition, ensureNotWatched, openApp, tid } from './helpers';

const TICKER = 'PYPL';

test.describe('watchlist', () => {
  test.beforeEach(async ({ request }) => {
    await closePosition(request, TICKER);
    await ensureNotWatched(request, TICKER);
  });
  test.afterEach(async ({ request }) => {
    await ensureNotWatched(request, TICKER);
  });

  test('add a ticker through the UI, see it priced, then remove it', async ({ page, request }) => {
    await openApp(page);
    await expect(tid(page, `watchlist-row-${TICKER}`)).toHaveCount(0);

    await tid(page, 'watchlist-add-input').fill(TICKER.toLowerCase());
    await tid(page, 'watchlist-add-button').click();

    await expect(tid(page, `watchlist-row-${TICKER}`)).toBeVisible();
    await expect(tid(page, `price-${TICKER}`)).toHaveText(/\d+\.\d{2}/, { timeout: 15_000 });
    const list = await (await request.get('/api/watchlist')).json();
    expect(list.tickers.map((t: { ticker: string }) => t.ticker)).toContain(TICKER);

    await tid(page, `watchlist-remove-${TICKER}`).click();
    await expect(tid(page, `watchlist-row-${TICKER}`)).toHaveCount(0);
    const after = await (await request.get('/api/watchlist')).json();
    expect(after.tickers.map((t: { ticker: string }) => t.ticker)).not.toContain(TICKER);

    // Stays removed after a reload (persisted server-side).
    await page.reload();
    await expect(tid(page, 'watchlist-row-AAPL')).toBeVisible();
    await expect(tid(page, `watchlist-row-${TICKER}`)).toHaveCount(0);
  });
});
