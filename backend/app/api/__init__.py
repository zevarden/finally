"""REST API routers (TEAM_CONTRACT.md §5). The chat router lives in app.llm."""

from .health import create_health_router
from .portfolio import create_portfolio_router
from .watchlist import create_watchlist_router

__all__ = ["create_health_router", "create_portfolio_router", "create_watchlist_router"]
