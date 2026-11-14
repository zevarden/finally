"""Service-layer exceptions. Routes map these to HTTP errors; the LLM layer reports them."""

from __future__ import annotations


class ServiceError(Exception):
    """Base class. `str(err)` is a human-readable message; `.status_code` an HTTP hint."""

    status_code: int = 400

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class TradeError(ServiceError):
    """A trade was rejected (bad input, no price, insufficient cash or shares). Always 400."""


class WatchlistError(ServiceError):
    """A watchlist change was rejected. 400 invalid, 404 unknown / not listed, 409 duplicate."""
