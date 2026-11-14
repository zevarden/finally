"""Portfolio valuation, trade execution and snapshots."""

from __future__ import annotations

import logging
import math

from app.market import MarketDataSource, PriceCache

from .errors import TradeError
from .tickers import untrack_if_unused, validate_ticker

logger = logging.getLogger(__name__)

# Float tolerance for quantity/cash comparisons (e.g. "sell all" of 0.1 + 0.2 shares).
EPSILON = 1e-6


def _round_money(x: float) -> float:
    return round(x, 2)


def _pct(numerator: float, denominator: float) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def get_portfolio(db, cache: PriceCache) -> dict:
    """Current portfolio. A position without a cached price is valued at its avg_cost."""
    cash = db.get_cash()
    rows = []
    for pos in db.list_positions():
        qty = float(pos["quantity"])
        avg = float(pos["avg_cost"])
        price = cache.get_price(pos["ticker"])
        current = float(price) if price is not None else avg
        market_value = qty * current
        cost_basis = qty * avg
        rows.append(
            {
                "ticker": pos["ticker"],
                "quantity": qty,
                "avg_cost": round(avg, 4),
                "current_price": current,
                "market_value": market_value,
                "cost_basis": cost_basis,
                "unrealized_pnl": market_value - cost_basis,
            }
        )

    positions_value = sum(r["market_value"] for r in rows)
    cost_total = sum(r["cost_basis"] for r in rows)
    total_value = cash + positions_value
    pnl_total = positions_value - cost_total

    rows.sort(key=lambda r: r["market_value"], reverse=True)
    positions = [
        {
            "ticker": r["ticker"],
            "quantity": r["quantity"],
            "avg_cost": r["avg_cost"],
            "current_price": r["current_price"],
            "market_value": _round_money(r["market_value"]),
            "unrealized_pnl": _round_money(r["unrealized_pnl"]),
            "unrealized_pnl_percent": _pct(r["unrealized_pnl"], r["cost_basis"]),
            "weight": round(r["market_value"] / total_value, 4) if total_value > 0 else 0.0,
        }
        for r in rows
    ]
    return {
        "cash_balance": _round_money(cash),
        "positions_value": _round_money(positions_value),
        "total_value": _round_money(total_value),
        "unrealized_pnl": _round_money(pnl_total),
        "unrealized_pnl_percent": _pct(pnl_total, cost_total),
        "positions": positions,
    }


def record_snapshot(db, cache: PriceCache) -> dict:
    """Store the current total portfolio value."""
    total = get_portfolio(db, cache)["total_value"]
    return db.insert_snapshot(total)


def _parse_quantity(quantity: object) -> float:
    if isinstance(quantity, bool):
        raise TradeError("Quantity must be a number")
    try:
        q = float(quantity)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TradeError("Quantity must be a number") from None
    if not math.isfinite(q) or q <= 0:
        raise TradeError("Quantity must be greater than zero")
    return q


async def execute_trade(
    db,
    cache: PriceCache,
    source: MarketDataSource,
    ticker: str,
    side: str,
    quantity: float,
) -> dict:
    """Execute a market order at the cached price.

    Returns {"trade": <trade row>, "portfolio": <get_portfolio()>}. Raises TradeError.
    """
    t = validate_ticker(ticker)
    if t is None:
        raise TradeError(f"Invalid ticker: {ticker!r}")
    s = side.strip().lower() if isinstance(side, str) else ""
    if s not in ("buy", "sell"):
        raise TradeError("Side must be 'buy' or 'sell'")
    qty = _parse_quantity(quantity)

    price = cache.get_price(t)
    newly_tracked = False
    if price is None and s == "buy":
        # Not tracked yet (e.g. buying a ticker that isn't on the watchlist): ask the
        # source to price it. Tracking is undone below if the trade does not happen.
        await source.add_ticker(t)
        newly_tracked = True
        price = cache.get_price(t)
    if price is None or price <= 0:
        if newly_tracked:
            await untrack_if_unused(db, source, t)
        raise TradeError(f"No price available for {t}")

    try:
        trade, closed = _apply_trade(db, t, s, qty, float(price))
    except TradeError:
        if newly_tracked:
            await untrack_if_unused(db, source, t)
        raise

    if s == "buy" and t not in source.get_tickers():
        await source.add_ticker(t)
    if closed:
        await untrack_if_unused(db, source, t)

    try:
        record_snapshot(db, cache)
    except Exception:  # a snapshot failure must not fail a completed trade
        logger.exception("Snapshot after trade failed")

    return {"trade": trade, "portfolio": get_portfolio(db, cache)}


def _apply_trade(db, t: str, s: str, qty: float, price: float) -> tuple[dict, bool]:
    """Validate and apply the trade atomically. Returns (trade row, position_closed)."""
    closed = False
    with db.transaction():
        cash = db.get_cash()
        pos = db.get_position(t)
        held = float(pos["quantity"]) if pos else 0.0
        if s == "buy":
            cost = qty * price
            if cost > cash + EPSILON:
                raise TradeError(
                    f"Insufficient cash: buying {qty:g} {t} costs ${cost:,.2f}, "
                    f"you have ${cash:,.2f}"
                )
            new_qty = held + qty
            avg = (held * float(pos["avg_cost"]) + cost) / new_qty if pos else price
            db.set_cash(max(cash - cost, 0.0))
            db.upsert_position(t, new_qty, avg)
        else:
            if pos is None or qty > held + EPSILON:
                raise TradeError(f"Insufficient shares: you hold {held:g} {t}, tried to sell {qty:g}")
            remaining = held - qty
            if remaining <= EPSILON:
                qty = held  # sell exactly what is held
                db.delete_position(t)
                closed = True
            else:
                db.upsert_position(t, remaining, float(pos["avg_cost"]))
            db.set_cash(cash + qty * price)
        trade = db.insert_trade(t, s, qty, price)
    return trade, closed
