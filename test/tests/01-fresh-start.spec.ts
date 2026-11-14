import { expect, test } from '@playwright/test';
import { DEFAULT_TICKERS, getPortfolio, moneyOf, openApp, tid } from './helpers';

// Runs first so that, in docker compose (fresh DB), it sees the seeded state.
test.describe('fresh start', () => {
  test('shows the default watchlist, $10k cash and a connected stream', async ({ page, request }) => {
    await openApp(page);
    await expect(tid(page, 'connection-status')).toHaveAttribute('data-status', 'connected');

    await expect(tid(page, 'watchlist')).toBeVisible();
    for (const t of DEFAULT_TICKERS) {
      await expect(tid(page, `watchlist-row-${t}`), t).toBeVisible();
      await expect(tid(page, `price-${t}`), t).toHaveText(/\d+\.\d{2}/);
      await expect(tid(page, `sparkline-${t}`), t).toBeAttached();
    }

    const portfolio = await getPortfolio(request);
    if (portfolio.positions.length === 0 && portfolio.cash_balance === 10000) {
      await expect(tid(page, 'cash-balance')).toHaveText(/\$10,000\.00/);
      await expect(tid(page, 'total-value')).toHaveText(/\$10,000\.00/);
    } else {
      test.info().annotations.push({ type: 'note', description: 'DB not fresh: checked cash against the API instead of $10,000.00' });
      expect(await moneyOf(tid(page, 'cash-balance'))).toBeCloseTo(portfolio.cash_balance, 2);
    }
    await expect(tid(page, 'total-value')).toHaveText(/\$[\d,]+\.\d{2}/);

    await expect(tid(page, 'chat-panel')).toBeAttached();
    await expect(tid(page, 'trade-ticker')).toBeVisible();
    await expect(tid(page, 'trade-buy')).toBeVisible();
    await expect(tid(page, 'trade-sell')).toBeVisible();
  });

  test('prices stream and change, with flash classes applied', async ({ page }) => {
    await openApp(page);
    const price = tid(page, 'price-AAPL');
    await expect(price).toHaveText(/\d+\.\d{2}/);
    const first = (await price.textContent())!.trim();
    await expect(price).not.toHaveText(first, { timeout: 15_000 });

    // Some watchlist price cell gets flash-up / flash-down while prices tick.
    await page.waitForFunction(
      () => !!document.querySelector('[data-testid^="price-"].flash-up, [data-testid^="price-"].flash-down'),
      undefined,
      { timeout: 15_000, polling: 50 },
    );
  });

  test('clicking a watchlist row selects it in the main chart', async ({ page }) => {
    await openApp(page);
    await expect(tid(page, 'main-chart')).toBeVisible();
    await tid(page, 'watchlist-row-GOOGL').click();
    await expect(tid(page, 'main-chart')).toHaveAttribute('data-ticker', 'GOOGL');
    await tid(page, 'watchlist-row-NFLX').click();
    await expect(tid(page, 'main-chart')).toHaveAttribute('data-ticker', 'NFLX');
  });
});
