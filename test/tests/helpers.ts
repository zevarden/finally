import { expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';

export const DEFAULT_TICKERS = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA', 'NVDA', 'META', 'JPM', 'V', 'NFLX'];

export const tid = (page: Page, id: string): Locator => page.getByTestId(id);

/** "$10,050.25" -> 10050.25 */
export function parseMoney(text: string | null): number {
  const n = Number((text ?? '').replace(/[^0-9.\-]/g, ''));
  if (Number.isNaN(n)) throw new Error(`not a money value: ${text}`);
  return n;
}

export async function moneyOf(locator: Locator): Promise<number> {
  return parseMoney(await locator.textContent());
}

/** Load the app and wait until the price stream is up and the header is populated. */
export async function openApp(page: Page): Promise<void> {
  await page.goto('/');
  await expect(tid(page, 'connection-status')).toHaveAttribute('data-status', 'connected', { timeout: 20_000 });
  await expect(tid(page, 'cash-balance')).toHaveText(/\$[\d,]+\.\d{2}/);
}

export interface Position {
  ticker: string; quantity: number; avg_cost: number; current_price: number;
  market_value: number; unrealized_pnl: number; unrealized_pnl_percent: number; weight: number;
}
export interface Portfolio {
  cash_balance: number; positions_value: number; total_value: number;
  unrealized_pnl: number; unrealized_pnl_percent: number; positions: Position[];
}

export async function getPortfolio(request: APIRequestContext): Promise<Portfolio> {
  const r = await request.get('/api/portfolio');
  expect(r.status()).toBe(200);
  return r.json();
}

export async function heldQuantity(request: APIRequestContext, ticker: string): Promise<number> {
  const p = await getPortfolio(request);
  return p.positions.find((x) => x.ticker === ticker)?.quantity ?? 0;
}

export async function apiTrade(request: APIRequestContext, ticker: string, side: 'buy' | 'sell', quantity: number) {
  const r = await request.post('/api/portfolio/trade', { data: { ticker, side, quantity } });
  expect(r.status(), await r.text()).toBe(200);
  return r.json();
}

/** Sell everything held in `ticker` so a test starts with no position. */
export async function closePosition(request: APIRequestContext, ticker: string): Promise<void> {
  const qty = await heldQuantity(request, ticker);
  if (qty > 0) await apiTrade(request, ticker, 'sell', qty);
}

export async function ensureNotWatched(request: APIRequestContext, ticker: string): Promise<void> {
  const r = await request.delete(`/api/watchlist/${ticker}`);
  expect([204, 404]).toContain(r.status());
}

export async function ensureWatched(request: APIRequestContext, ticker: string): Promise<void> {
  const r = await request.post('/api/watchlist', { data: { ticker } });
  expect([201, 409]).toContain(r.status());
}

/** Make sure the chat input is visible (expand the sidebar if collapsed). */
export async function openChat(page: Page): Promise<void> {
  const input = tid(page, 'chat-input');
  if (!(await input.isVisible())) await tid(page, 'chat-toggle').click();
  await expect(input).toBeVisible();
}

/** Send a chat message and return the new assistant bubble. */
export async function sendChat(page: Page, message: string): Promise<Locator> {
  await openChat(page);
  const assistant = tid(page, 'chat-message').and(page.locator('[data-role="assistant"]'));
  const before = await assistant.count();
  await tid(page, 'chat-input').fill(message);
  await tid(page, 'chat-send').click();
  await expect(assistant).toHaveCount(before + 1, { timeout: 15_000 });
  return assistant.nth(before);
}

/** Matches a quantity rendered as e.g. "3", "3.0", "3.00" or "3.0000" as a whole token. */
export const qtyRegex = (n: number) => new RegExp(`(^|[^\d.])${n}(\.0+)?($|[^\d.])`);
