"""Fake Massive client built on the library's REAL response models.

Do not use MagicMock for snapshots: it invents any attribute you ask for, which is
how the `last_trade.timestamp` bug (the real field is `sip_timestamp`, in ns) got through.
"""

from __future__ import annotations

from massive.exceptions import BadResponse
from massive.rest.models import GroupedDailyAgg, TickerSnapshot

PLAN_403 = (
    '{"status":"NOT_AUTHORIZED","request_id":"x","message":"You are not entitled to this data. '
    'Please upgrade your plan at https://massive.com/pricing"}'
)
AUTH_401 = '{"status":"ERROR","request_id":"x","error":"Unknown API Key"}'
RATE_429 = (
    '{"status":"ERROR","request_id":"x","error":"You\'ve exceeded the maximum requests per '
    'minute, please wait or upgrade your subscription to continue."}'
)
TS_NS = 1_763_000_000_000_000_000


def snap(
    ticker: str,
    price: float | None = None,
    prev_close: float | None = None,
    day_close: float | None = None,
    min_close: float | None = None,
    ts_ns: int = TS_NS,
) -> TickerSnapshot:
    d: dict = {"ticker": ticker}
    if price is not None:
        d["lastTrade"] = {"p": price, "t": ts_ns}
    if prev_close is not None:
        d["prevDay"] = {"c": prev_close}
    if day_close is not None:
        d["day"] = {"c": day_close}
    if min_close is not None:
        d["min"] = {"c": min_close, "t": 1_763_000_000_000}
    return TickerSnapshot.from_dict(d)


class FakeClient:
    """Mimics the RESTClient methods MassiveDataSource uses."""

    def __init__(self, prices=None, snapshot_error=None, grouped=None):
        self.prices = prices or {}  # ticker -> (price, prev_close)
        self.snapshot_error = snapshot_error  # body to raise as BadResponse
        self.grouped = grouped or {}  # "YYYY-MM-DD" -> {ticker: close}
        self.calls: list[tuple] = []
        self.before_return = None  # optional hook, runs inside get_snapshot_all

    def get_snapshot_all(self, market_type, tickers):
        self.calls.append(("all", tuple(tickers)))
        if self.snapshot_error:
            raise BadResponse(self.snapshot_error)
        result = [snap(t, *self.prices[t]) for t in tickers if t in self.prices]
        if self.before_return:
            self.before_return()
        return result

    def get_snapshot_ticker(self, market_type, ticker):
        self.calls.append(("one", ticker))
        if self.snapshot_error:
            raise BadResponse(self.snapshot_error)
        if ticker not in self.prices:
            raise BadResponse('{"status":"NOT_FOUND","message":"Ticker not found."}')
        return snap(ticker, *self.prices[ticker])

    def get_grouped_daily_aggs(self, d, adjusted=True):
        self.calls.append(("grouped", d))
        return [
            GroupedDailyAgg(ticker=t, close=c, timestamp=1_762_900_000_000)
            for t, c in self.grouped.get(d, {}).items()
        ]
