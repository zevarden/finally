import { expect, test } from '@playwright/test';
import { apiTrade, closePosition, heldQuantity, moneyOf, openApp, qtyRegex, tid } from './helpers';

test.describe('trading', () => {
  test('buy: cash decreases and a position row appears', async ({ page, request }) => {
    const T = 'MSFT';
    await closePosition(request, T);
    await openApp(page);
    await expect(tid(page, `position-row-${T}`)).toHaveCount(0);
    const cashBefore = await moneyOf(tid(page, 'cash-balance'));

    await tid(page, 'trade-ticker').fill(T);
    await tid(page, 'trade-quantity').fill('2');
    await tid(page, 'trade-buy').click();

    const row = tid(page, `position-row-${T}`);
    await expect(row).toBeVisible();
    await expect(row).toContainText(qtyRegex(2));
    await expect.poll(() => moneyOf(tid(page, 'cash-balance'))).toBeLessThan(cashBefore);
    expect(await heldQuantity(request, T)).toBe(2);
    await expect(tid(page, 'trade-error')).toBeHidden();

    await closePosition(request, T);
  });

  test('sell: cash increases, the position shrinks then disappears', async ({ page, request }) => {
    const T = 'JPM';
    await closePosition(request, T);
    await apiTrade(request, T, 'buy', 3);
    await openApp(page);

    const row = tid(page, `position-row-${T}`);
    await expect(row).toContainText(qtyRegex(3));
    const cashBefore = await moneyOf(tid(page, 'cash-balance'));

    await tid(page, 'trade-ticker').fill(T);
    await tid(page, 'trade-quantity').fill('1');
    await tid(page, 'trade-sell').click();
    await expect(row).toContainText(qtyRegex(2));
    await expect.poll(() => moneyOf(tid(page, 'cash-balance'))).toBeGreaterThan(cashBefore);
    const cashMid = await moneyOf(tid(page, 'cash-balance'));

    await tid(page, 'trade-ticker').fill(T);
    await tid(page, 'trade-quantity').fill('2');
    await tid(page, 'trade-sell').click();
    await expect(row).toHaveCount(0);
    await expect.poll(() => moneyOf(tid(page, 'cash-balance'))).toBeGreaterThan(cashMid);
    expect(await heldQuantity(request, T)).toBe(0);
  });

  test('selling more than owned shows a trade error and changes nothing', async ({ page, request }) => {
    const T = 'V';
    await closePosition(request, T);
    await openApp(page);
    const cashBefore = await moneyOf(tid(page, 'cash-balance'));

    await tid(page, 'trade-ticker').fill(T);
    await tid(page, 'trade-quantity').fill('5');
    await tid(page, 'trade-sell').click();

    await expect(tid(page, 'trade-error')).toBeVisible();
    await expect(tid(page, 'trade-error')).not.toBeEmpty();
    await expect(tid(page, `position-row-${T}`)).toHaveCount(0);
    expect(await moneyOf(tid(page, 'cash-balance'))).toBeCloseTo(cashBefore, 2);
  });

  test('buying with insufficient cash shows a trade error', async ({ page, request }) => {
    await closePosition(request, 'AMZN');
    await openApp(page);
    await tid(page, 'trade-ticker').fill('AMZN');
    await tid(page, 'trade-quantity').fill('1000000');
    await tid(page, 'trade-buy').click();
    await expect(tid(page, 'trade-error')).toBeVisible();
    await expect(tid(page, 'position-row-AMZN')).toHaveCount(0);
  });
});
