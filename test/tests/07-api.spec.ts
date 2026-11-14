import { expect, test } from '@playwright/test';
import { apiTrade, closePosition, ensureNotWatched, ensureWatched, getPortfolio } from './helpers';

const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/;

test.describe('REST API (contract §5)', () => {
  test('GET /api/health', async ({ request }) => {
    const r = await request.get('/api/health');
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body.status).toBe('ok');
    expect(body.market).toEqual(expect.objectContaining({ source: 'simulator' }));
  });

  test('GET /api/portfolio shape', async ({ request }) => {
    const p = await getPortfolio(request);
    for (const k of ['cash_balance', 'positions_value', 'total_value', 'unrealized_pnl', 'unrealized_pnl_percent'] as const) {
      expect(typeof p[k], k).toBe('number');
    }
    expect(Array.isArray(p.positions)).toBe(true);
    // Each figure is rounded to cents independently, so allow a one-cent difference.
    expect(Math.abs(p.total_value - (p.cash_balance + p.positions_value))).toBeLessThanOrEqual(0.0101);
  });

  test('POST /api/portfolio/trade buy/sell round trip, positions shape and ordering', async ({ request }) => {
    await closePosition(request, 'GOOGL');
    await closePosition(request, 'NFLX');
    const cash0 = (await getPortfolio(request)).cash_balance;

    const buy = await apiTrade(request, 'googl', 'buy', 2);
    expect(buy.trade).toEqual(
      expect.objectContaining({ ticker: 'GOOGL', side: 'buy', quantity: 2, price: expect.any(Number), id: expect.any(String) }),
    );
    expect(buy.trade.executed_at).toMatch(ISO);
    expect(buy.portfolio.cash_balance).toBeCloseTo(cash0 - 2 * buy.trade.price, 2);

    const pos = buy.portfolio.positions.find((x: { ticker: string }) => x.ticker === 'GOOGL');
    expect(pos).toEqual(
      expect.objectContaining({
        ticker: 'GOOGL', quantity: 2, avg_cost: buy.trade.price, current_price: expect.any(Number),
        market_value: expect.any(Number), unrealized_pnl: expect.any(Number),
        unrealized_pnl_percent: expect.any(Number), weight: expect.any(Number),
      }),
    );
    expect(pos.weight).toBeGreaterThan(0);
    expect(pos.weight).toBeLessThanOrEqual(1);

    await apiTrade(request, 'NFLX', 'buy', 0.25);
    const p = await getPortfolio(request);
    const mv = p.positions.map((x) => x.market_value);
    expect(mv).toEqual([...mv].sort((a, b) => b - a));

    const sell = await apiTrade(request, 'GOOGL', 'sell', 2);
    expect(sell.trade.side).toBe('sell');
    expect(sell.portfolio.positions.find((x: { ticker: string }) => x.ticker === 'GOOGL')).toBeUndefined();
    await closePosition(request, 'NFLX');
  });

  test('POST /api/portfolio/trade errors: 400 and 422', async ({ request }) => {
    await closePosition(request, 'AMZN');
    const cases: Array<[unknown, number]> = [
      [{ ticker: 'AMZN', side: 'sell', quantity: 1 }, 400], // insufficient shares
      [{ ticker: 'AMZN', side: 'buy', quantity: 1e9 }, 400], // insufficient cash
      [{ ticker: 'AMZN', side: 'buy', quantity: 0 }, 400],
      [{ ticker: 'AMZN', side: 'buy', quantity: -1 }, 400],
      [{ ticker: '$$$', side: 'buy', quantity: 1 }, 400],
    ];
    for (const [data, code] of cases) {
      const r = await request.post('/api/portfolio/trade', { data });
      expect(r.status(), JSON.stringify(data)).toBe(code);
      expect(typeof (await r.json()).detail, JSON.stringify(data)).toBe('string');
    }
    for (const data of [{ ticker: 'AMZN', side: 'hold', quantity: 1 }, { ticker: 'AMZN' }, { quantity: 1, side: 'buy' }]) {
      const r = await request.post('/api/portfolio/trade', { data });
      // An invalid side may be rejected by the model (422) or by the service (400).
      expect([400, 422], JSON.stringify(data)).toContain(r.status());
    }
    const bad = await request.post('/api/portfolio/trade', { data: { ticker: 'AMZN', side: 'buy' } });
    expect(bad.status()).toBe(422);
  });

  test('GET /api/portfolio/history', async ({ request }) => {
    await apiTrade(request, 'AAPL', 'buy', 0.1); // guarantees a snapshot
    const r = await request.get('/api/portfolio/history');
    expect(r.status()).toBe(200);
    const { snapshots } = await r.json();
    expect(snapshots.length).toBeGreaterThan(0);
    for (const s of snapshots) {
      expect(typeof s.total_value).toBe('number');
      expect(s.recorded_at).toMatch(ISO);
    }
    const times = snapshots.map((s: { recorded_at: string }) => Date.parse(s.recorded_at));
    expect(times).toEqual([...times].sort((a: number, b: number) => a - b));
    await apiTrade(request, 'AAPL', 'sell', 0.1);
  });

  test('watchlist CRUD and status codes', async ({ request }) => {
    const T = 'SHOP';
    await ensureNotWatched(request, T);

    const list = await request.get('/api/watchlist');
    expect(list.status()).toBe(200);
    const { tickers } = await list.json();
    expect(tickers.length).toBeGreaterThan(0);
    for (const k of ['ticker', 'added_at', 'price', 'previous_price', 'change_percent', 'day_change_percent', 'direction']) {
      expect(tickers[0], k).toHaveProperty(k);
    }

    const add = await request.post('/api/watchlist', { data: { ticker: ' shop ' } });
    expect(add.status()).toBe(201);
    const entry = await add.json();
    expect(entry.ticker).toBe(T);
    expect(entry.added_at).toMatch(ISO);
    expect(typeof entry.price).toBe('number'); // simulator prices new tickers immediately

    expect((await request.post('/api/watchlist', { data: { ticker: T } })).status()).toBe(409);
    expect((await request.post('/api/watchlist', { data: { ticker: '!!bad!!' } })).status()).toBe(400);
    expect((await request.post('/api/watchlist', { data: {} })).status()).toBe(422);

    const del = await request.delete(`/api/watchlist/${T}`);
    expect(del.status()).toBe(204);
    expect((await request.delete(`/api/watchlist/${T}`)).status()).toBe(404);
    const after = await (await request.get('/api/watchlist')).json();
    expect(after.tickers.map((t: { ticker: string }) => t.ticker)).not.toContain(T);
  });

  test('POST /api/chat and GET /api/chat/history (mock mode)', async ({ request }) => {
    await closePosition(request, 'AMD');
    const r = await request.post('/api/chat', { data: { message: 'buy 1 AMD' } });
    expect(r.status()).toBe(200);
    const msg = await r.json();
    expect(msg).toEqual(expect.objectContaining({ id: expect.any(String), role: 'assistant', content: 'Mock: buying 1 AMD.' }));
    expect(msg.created_at).toMatch(ISO);
    expect(msg.actions.trades).toHaveLength(1);
    expect(msg.actions.trades[0]).toEqual(
      expect.objectContaining({ ticker: 'AMD', side: 'buy', quantity: 1, status: 'executed', price: expect.any(Number), error: null }),
    );

    const fail = await (await request.post('/api/chat', { data: { message: 'SELL 1000 AMD' } })).json();
    expect(fail.content).toBe('Mock: selling 1000 AMD.');
    expect(fail.actions.trades[0].status).toBe('failed');
    expect(typeof fail.actions.trades[0].error).toBe('string');

    const dup = await (await request.post('/api/chat', { data: { message: 'add AAPL' } })).json();
    expect(dup.actions.watchlist_changes[0]).toEqual(expect.objectContaining({ ticker: 'AAPL', action: 'add', status: 'failed' }));

    const plain = await (await request.post('/api/chat', { data: { message: 'how am I doing?' } })).json();
    expect(plain.content).toMatch(/^Mock response: I'm FinAlly, your AI trading assistant\. You have \$[\d,]+\.\d{2} in cash\.$/);
    expect(plain.actions).toBeNull();

    const hist = await request.get('/api/chat/history');
    expect(hist.status()).toBe(200);
    const { messages } = await hist.json();
    expect(messages.length).toBeLessThanOrEqual(50);
    const lastTwo = messages.slice(-2);
    expect(lastTwo[0]).toEqual(expect.objectContaining({ role: 'user', content: 'how am I doing?', actions: null }));
    expect(lastTwo[1]).toEqual(expect.objectContaining({ role: 'assistant', id: plain.id }));

    expect((await request.post('/api/chat', { data: {} })).status()).toBe(422);
    await closePosition(request, 'AMD');
    // AMD was only tracked because it was held; closing it should leave the watchlist untouched.
    await ensureNotWatched(request, 'AMD');
  });

  test('GET /api/stream/prices sends SSE frames with the full price map', async ({ page }) => {
    await page.goto('/api/health');
    const frame = await page.evaluate(async () => {
      const ctrl = new AbortController();
      const res = await fetch('/api/stream/prices', { signal: ctrl.signal });
      const contentType = res.headers.get('content-type');
      const reader = res.body!.getReader();
      const dec = new TextDecoder();
      let buf = '';
      const deadline = Date.now() + 10_000;
      while (Date.now() < deadline) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const m = buf.match(/^data: (.+)$/m);
        if (m && m[1] !== '{}') {
          ctrl.abort();
          return { contentType, data: JSON.parse(m[1]) };
        }
      }
      ctrl.abort();
      return { contentType, data: null };
    });
    expect(frame.contentType).toContain('text/event-stream');
    expect(frame.data).not.toBeNull();
    const aapl = frame.data.AAPL;
    expect(aapl).toEqual(
      expect.objectContaining({
        ticker: 'AAPL', price: expect.any(Number), previous_price: expect.any(Number), timestamp: expect.any(Number),
        direction: expect.any(String),
      }),
    );
    for (const k of ['change', 'change_percent', 'session_open', 'day_change', 'day_change_percent']) {
      expect(aapl, k).toHaveProperty(k);
    }
  });

  test('static frontend is served at / and unknown API paths 404', async ({ request }) => {
    const root = await request.get('/');
    expect(root.status()).toBe(200);
    expect(root.headers()['content-type']).toContain('text/html');
    expect((await request.get('/api/does-not-exist')).status()).toBe(404);
  });

  test('ensureWatched helper leaves defaults intact', async ({ request }) => {
    // Re-adding a default ticker is a 409, never a duplicate.
    await ensureWatched(request, 'AAPL');
    const { tickers } = await (await request.get('/api/watchlist')).json();
    expect(tickers.filter((t: { ticker: string }) => t.ticker === 'AAPL')).toHaveLength(1);
  });
});
