"""LLM chat layer for FinAlly (TEAM_CONTRACT.md §6).

Public API:
    create_chat_router  - APIRouter with POST /api/chat and GET /api/chat/history
    LLMResponse, TradeAction, WatchlistChange, parse_llm_response - structured output
    mock_response       - deterministic LLM_MOCK=true responses
"""

from .mock import mock_response
from .models import LLMResponse, ParseError, TradeAction, WatchlistChange, parse_llm_response
from .router import create_chat_router

__all__ = [
    "create_chat_router",
    "LLMResponse",
    "TradeAction",
    "WatchlistChange",
    "ParseError",
    "parse_llm_response",
    "mock_response",
]
