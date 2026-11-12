"""Massive (formerly Polygon.io) REST data source."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any, Literal

from massive import RESTClient
from massive.exceptions import BadResponse
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource, normalize_ticker

logger = logging.getLogger(__name__)

Mode = Literal["starting", "live", "eod"]
ErrorKind = Literal["auth", "plan", "rate_limit", "other"]

EOD_REFRESH_SECONDS = 3600.0
EOD_MAX_LOOKBACK_DAYS = 10


class MassiveAuthError(RuntimeError):
    """The API key was rejected (401). start_market_data() falls back to the simulator."""


# --- pure helpers (unit-tested directly) -------------------------------------


def classify_error(exc: BaseException) -> ErrorKind:
    """Map a massive BadResponse (whose message is the raw JSON body) to a kind."""
    if not isinstance(exc, BadResponse):
        return "other"
    text = str(exc)
    try:
        body = json.loads(text)
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    status = str(body.get("status", "")).upper()
    detail = f"{body.get('error', '')} {body.get('message', '')} {text}".lower()
    if "api key" in detail or (status != "NOT_AUTHORIZED" and "unauthorized" in detail):
        return "auth"
    if status == "NOT_AUTHORIZED" or "not entitled" in detail or "upgrade your plan" in detail:
        return "plan"
    if "exceeded" in detail or "too many" in detail:
        return "rate_limit"
    return "other"


def extract_price(snap: Any) -> tuple[float, float | None] | None:
    """(price, unix_seconds | None) from a TickerSnapshot, or None if no usable price.

    Prefers the last trade; falls back to bar closes, e.g. after the overnight
    snapshot reset (~3:30 AM ET) when last_trade is empty.
    """
    trade = getattr(snap, "last_trade", None)
    if trade is not None and trade.price:
        ts = trade.sip_timestamp / 1e9 if trade.sip_timestamp else None  # ns -> s
        return float(trade.price), ts
    minute = getattr(snap, "min", None)
    if minute is not None and minute.close:
        ts = minute.timestamp / 1e3 if minute.timestamp else None  # ms -> s
        return float(minute.close), ts
    for bar in (getattr(snap, "day", None), getattr(snap, "prev_day", None)):
        if bar is not None and bar.close:
            return float(bar.close), None
    return None


def extract_session_open(snap: Any) -> float | None:
    """Reference price for daily change %: the previous session's close."""
    prev = getattr(snap, "prev_day", None)
    return float(prev.close) if prev is not None and prev.close else None


class MassiveDataSource(MarketDataSource):
    """Polls Massive and writes prices to the PriceCache.

    live mode: GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=... every poll_interval
    eod mode:  GET /v2/aggs/grouped/locale/us/market/stocks/{date} hourly (free Basic plan)
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
        client: Any | None = None,  # inject a fake in tests
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = max(1.0, poll_interval)
        self._client = client
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._mode: Mode = "starting"
        # eod mode: ticker -> (close, previous_close, bar_timestamp_s)
        self._eod_bars: dict[str, tuple[float, float | None, float | None]] = {}
        self._missing_logged: set[str] = set()
        self._last_success: float | None = None
        self._last_error: str | None = None

    # --- lifecycle ----------------------------------------------------------

    async def start(
        self, tickers: list[str], initial_prices: dict[str, float] | None = None
    ) -> None:
        # initial_prices is ignored: real data needs no seed.
        self._tickers = list(dict.fromkeys(normalize_ticker(t) for t in [*self._tickers, *tickers]))
        if self._client is None:
            # retries=0: our poll loop is the retry; the client's own 429 retries
            # would burn the free tier's 5 calls/min.
            self._client = RESTClient(api_key=self._api_key, retries=0)

        await self._probe()  # sets mode and seeds the cache; may raise MassiveAuthError
        self._task = asyncio.create_task(self._loop(), name="massive-poller")
        logger.info(
            "Massive started: mode=%s, %d tickers, interval=%.0fs",
            self._mode,
            len(self._tickers),
            self._current_interval(),
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Massive stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self._tickers:
            return
        self._tickers.append(ticker)
        if self._mode == "starting":  # not started yet: start() will fetch it
            return
        try:
            if self._mode == "eod":
                self._write_eod(ticker)
            else:
                snap = await asyncio.to_thread(
                    self._client.get_snapshot_ticker, SnapshotMarketType.STOCKS, ticker
                )
                self._write_snapshot(snap)
        except Exception as e:  # unknown ticker (404) or transient; next poll retries
            logger.warning("Massive: immediate fetch for %s failed: %s", ticker, e)
        logger.info("Massive: added %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._missing_logged.discard(ticker)
        self._cache.remove(ticker)
        logger.info("Massive: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    def status(self) -> dict[str, Any]:
        return {
            "source": "massive",
            "mode": self._mode,
            "tickers": len(self._tickers),
            "poll_interval": self._current_interval(),
            "last_success": self._last_success,
            "last_error": self._last_error,
        }

    # --- probe & loop -------------------------------------------------------

    async def _probe(self) -> None:
        """First poll doubles as a capability check: 200 -> live, 403 -> eod, 401 -> raise."""
        try:
            await self._poll_live()
            self._mode = "live"
        except BadResponse as e:
            kind = classify_error(e)
            if kind == "auth":
                raise MassiveAuthError(str(e)) from e
            if kind == "plan":
                logger.warning(
                    "Massive key has no snapshot access (free/Basic plan); "
                    "using end-of-day closes, refreshed hourly"
                )
                self._mode = "eod"
                await self._safe(self._poll_eod)
            else:
                self._record_error(e)
                self._mode = "live"  # assume live; the loop keeps retrying
        except Exception as e:  # network down at boot: keep the app up
            self._record_error(e)
            self._mode = "live"

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._current_interval())
            await self._safe(self._poll_eod if self._mode == "eod" else self._poll_live)

    async def _safe(self, fn: Callable[[], Awaitable[None]]) -> None:
        try:
            await fn()
        except Exception as e:
            self._record_error(e)

    def _current_interval(self) -> float:
        return EOD_REFRESH_SECONDS if self._mode == "eod" else self._interval

    def _record_error(self, e: BaseException) -> None:
        kind = classify_error(e)
        self._last_error = f"{kind}: {e}"[:300]
        logger.warning("Massive poll failed (%s): %s", kind, e)

    # --- live mode ----------------------------------------------------------

    async def _poll_live(self) -> None:
        tickers = list(self._tickers)  # copy: the list may change while the thread runs
        if not tickers:
            return
        snaps = await asyncio.to_thread(
            self._client.get_snapshot_all, SnapshotMarketType.STOCKS, tickers
        )
        seen = {s.ticker for s in snaps if self._write_snapshot(s)}
        for t in set(tickers) - seen - self._missing_logged:
            logger.warning("Massive returned no price for %s (unknown ticker?)", t)
            self._missing_logged.add(t)
        self._last_success = time.time()
        self._last_error = None

    def _write_snapshot(self, snap: Any) -> bool:
        ticker = getattr(snap, "ticker", None)
        if not ticker or ticker not in self._tickers:  # removed while in flight
            return False
        extracted = extract_price(snap)
        if extracted is None:
            return False
        price, ts = extracted
        self._cache.update(ticker, price, timestamp=ts, session_open=extract_session_open(snap))
        return True

    # --- eod mode -----------------------------------------------------------

    async def _poll_eod(self) -> None:
        bars = await asyncio.to_thread(self._fetch_last_two_sessions)
        if bars:
            self._eod_bars = bars
        for t in list(self._tickers):
            self._write_eod(t)
        self._last_success = time.time()
        self._last_error = None

    def _write_eod(self, ticker: str) -> None:
        bar = self._eod_bars.get(ticker)
        if bar is None or ticker not in self._tickers:
            return
        close, prev_close, ts = bar
        self._cache.update(ticker, close, timestamp=ts, session_open=prev_close)

    def _fetch_last_two_sessions(self) -> dict[str, tuple[float, float | None, float | None]]:
        """Sync (runs in a thread). Walk back from yesterday to the last two trading days.

        Each grouped-daily call returns every US stock (~10k rows) in one request.
        Weekends and holidays return an empty list. On Basic, today's bar is not
        available until after midnight ET, so we start from yesterday.
        """
        sessions: list[dict[str, Any]] = []
        d = date.today() - timedelta(days=1)
        for _ in range(EOD_MAX_LOOKBACK_DAYS):
            if len(sessions) == 2:
                break
            rows = self._client.get_grouped_daily_aggs(d.isoformat(), adjusted=True)
            if rows:
                sessions.append({r.ticker: r for r in rows if r.ticker and r.close})
            d -= timedelta(days=1)
        if not sessions:
            return {}
        latest, prior = sessions[0], (sessions[1] if len(sessions) > 1 else {})
        return {
            t: (
                float(r.close),
                float(prior[t].close) if t in prior else None,
                r.timestamp / 1e3 if r.timestamp else None,
            )
            for t, r in latest.items()
        }
