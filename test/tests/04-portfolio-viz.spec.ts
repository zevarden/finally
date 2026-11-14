import { expect, test } from '@playwright/test';
import { apiTrade, closePosition, getPortfolio, openApp, tid } from './helpers';

test.describe('portfolio visualizations', () => {
  test('heatmap cell has a P&L colour and the P&L chart has data points', async ({ page, request }) => {
    const T = 'NVDA';
    await closePosition(request, T);
    await apiTrade(request, T, 'buy', 1); // also records a snapshot
    await openApp(page);

    await expect(tid(page, 'heatmap')).toBeVisible();
    const cell = tid(page, `heatmap-cell-${T}`);
    await expect(cell).toBeVisible();
    await expect(cell).toHaveAttribute('data-pnl', /^(positive|negative|flat)$/);

    // The colour follows the sign of the P&L. Prices move, so poll until the
    // cell and the API agree (or the cell is "flat", which is acceptable near 0).
    await expect
      .poll(
        async () => {
          const pos = (await getPortfolio(request)).positions.find((p) => p.ticker === T)!;
          const want = pos.unrealized_pnl > 0 ? 'positive' : pos.unrealized_pnl < 0 ? 'negative' : 'flat';
          const got = await cell.getAttribute('data-pnl');
          return got === want || got === 'flat' ? 'consistent' : `${got} vs api ${want}`;
        },
        { timeout: 15_000 },
      )
      .toBe('consistent');

    const chart = tid(page, 'pnl-chart');
    await expect(chart).toBeVisible();
    await expect.poll(async () => Number(await chart.getAttribute('data-points'))).toBeGreaterThan(0);

    const history = await (await request.get('/api/portfolio/history')).json();
    expect(history.snapshots.length).toBeGreaterThan(0);

    await closePosition(request, T);
  });

  test('positions table lists held positions with fractional quantity', async ({ page, request }) => {
    const T = 'META';
    await closePosition(request, T);
    await apiTrade(request, T, 'buy', 1.5);
    await openApp(page);
    await expect(tid(page, 'positions-table')).toBeVisible();
    await expect(tid(page, `position-row-${T}`)).toContainText(T);
    await expect(tid(page, `position-row-${T}`)).toContainText('1.5');
    await closePosition(request, T);
    await page.reload();
    await expect(tid(page, 'positions-table')).toBeVisible();
    await expect(tid(page, `position-row-${T}`)).toHaveCount(0);
  });
});
