import { expect, test } from '@playwright/test';
import { closePosition, ensureNotWatched, heldQuantity, openApp, openChat, qtyRegex, sendChat, tid } from './helpers';

// Requires the backend to run with LLM_MOCK=true (contract §6).
test.describe('AI chat (mock mode)', () => {
  test('"buy 1 AAPL" executes a trade shown inline and the position appears', async ({ page, request }) => {
    await closePosition(request, 'AAPL');
    await openApp(page);

    const reply = await sendChat(page, 'buy 1 AAPL');
    await expect(reply).toContainText('Mock: buying 1 AAPL.');
    await expect(tid(page, 'chat-message').and(page.locator('[data-role="user"]')).last()).toContainText('buy 1 AAPL');

    const action = tid(page, 'chat-action').filter({ hasText: 'AAPL' }).last();
    await expect(action).toHaveAttribute('data-status', 'executed');

    await expect(tid(page, 'position-row-AAPL')).toBeVisible();
    await expect(tid(page, 'position-row-AAPL')).toContainText(qtyRegex(1));
    expect(await heldQuantity(request, 'AAPL')).toBe(1);

    await closePosition(request, 'AAPL');
  });

  test('"sell 1000 TSLA" reports a failed action and changes nothing', async ({ page, request }) => {
    await closePosition(request, 'TSLA');
    await openApp(page);

    const reply = await sendChat(page, 'sell 1000 TSLA');
    await expect(reply).toContainText('Mock: selling 1000 TSLA.');
    const action = tid(page, 'chat-action').filter({ hasText: 'TSLA' }).last();
    await expect(action).toHaveAttribute('data-status', 'failed');
    await expect(tid(page, 'position-row-TSLA')).toHaveCount(0);
    expect(await heldQuantity(request, 'TSLA')).toBe(0);
  });

  test('watchlist add/remove through chat', async ({ page, request }) => {
    const T = 'UBER';
    await closePosition(request, T);
    await ensureNotWatched(request, T);
    await openApp(page);

    const added = await sendChat(page, `add ${T}`);
    await expect(added).toContainText(`Mock: adding ${T} to your watchlist.`);
    await expect(tid(page, 'chat-action').filter({ hasText: T }).last()).toHaveAttribute('data-status', 'executed');
    await expect(tid(page, `watchlist-row-${T}`)).toBeVisible();

    const removed = await sendChat(page, `remove ${T}`);
    await expect(removed).toContainText(`Mock: removing ${T} from your watchlist.`);
    await expect(tid(page, 'chat-action').filter({ hasText: T }).last()).toHaveAttribute('data-status', 'executed');
    await expect(tid(page, `watchlist-row-${T}`)).toHaveCount(0);
  });

  test('plain message gets the mock greeting, with a loading indicator meanwhile', async ({ page }) => {
    await openApp(page);
    await openChat(page);
    // Hold the response briefly so the loading state is observable.
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    await page.route('**/api/chat', async (route) => {
      await held;
      await route.continue();
    });

    await tid(page, 'chat-input').fill('hello there');
    await tid(page, 'chat-send').click();
    await expect(tid(page, 'chat-loading')).toBeVisible();
    release();
    await expect(tid(page, 'chat-loading')).toBeHidden({ timeout: 15_000 });

    const last = tid(page, 'chat-message').and(page.locator('[data-role="assistant"]')).last();
    await expect(last).toContainText("Mock response: I'm FinAlly, your AI trading assistant.");
    await expect(last).toContainText(/You have \$[\d,]+\.\d{2} in cash\./);
    await page.unroute('**/api/chat');
  });

  test('chat history is reloaded after a page refresh', async ({ page }) => {
    await openApp(page);
    const marker = `history check ${Date.now()}`;
    await sendChat(page, marker);
    await page.reload();
    await openChat(page);
    await expect(tid(page, 'chat-message').filter({ hasText: marker })).toHaveCount(1);
  });
});
