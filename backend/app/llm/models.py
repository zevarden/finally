"""Structured-output schema for the LLM, plus parsing of raw model text."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.market import normalize_ticker


class TradeAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticker: str
    side: Literal["buy", "sell"]
    quantity: float

    @field_validator("ticker", mode="before")
    @classmethod
    def _ticker(cls, v: Any) -> Any:
        return normalize_ticker(v) if isinstance(v, str) else v

    @field_validator("side", mode="before")
    @classmethod
    def _side(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v


class WatchlistChange(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticker: str
    action: Literal["add", "remove"]

    @field_validator("ticker", mode="before")
    @classmethod
    def _ticker(cls, v: Any) -> Any:
        return normalize_ticker(v) if isinstance(v, str) else v

    @field_validator("action", mode="before")
    @classmethod
    def _action(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v


class LLMResponse(BaseModel):
    """What the LLM must return. `trades` and `watchlist_changes` are optional."""

    model_config = ConfigDict(extra="ignore")

    message: str
    trades: list[TradeAction] = Field(default_factory=list)
    watchlist_changes: list[WatchlistChange] = Field(default_factory=list)

    @field_validator("trades", "watchlist_changes", mode="before")
    @classmethod
    def _none_to_empty(cls, v: Any) -> Any:
        return [] if v is None else v


# JSON schema sent as `response_format`. Strict structured outputs require every
# property to be listed in `required`, so the arrays are required here (they may be
# empty) even though the parser above treats them as optional.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "message": {"type": "string", "description": "Conversational reply shown to the user"},
        "trades": {
            "type": "array",
            "description": "Market orders to execute immediately (empty if none)",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "side": {"type": "string", "enum": ["buy", "sell"]},
                    "quantity": {"type": "number"},
                },
                "required": ["ticker", "side", "quantity"],
                "additionalProperties": False,
            },
        },
        "watchlist_changes": {
            "type": "array",
            "description": "Watchlist modifications (empty if none)",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "action": {"type": "string", "enum": ["add", "remove"]},
                },
                "required": ["ticker", "action"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["message", "trades", "watchlist_changes"],
    "additionalProperties": False,
}

RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {"name": "finally_chat_response", "schema": RESPONSE_SCHEMA, "strict": True},
}


class ParseError(ValueError):
    """The model output was not valid JSON matching LLMResponse."""


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def parse_llm_response(raw: str | None) -> LLMResponse:
    """Parse raw model text into an LLMResponse. Raises ParseError on any problem."""
    if raw is None or not raw.strip():
        raise ParseError("empty response")
    text = raw.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ParseError("top-level JSON value is not an object")
    try:
        parsed = LLMResponse.model_validate(data)
    except ValidationError as exc:
        raise ParseError(f"schema mismatch: {exc.error_count()} error(s)") from exc
    if not parsed.message.strip():
        raise ParseError("empty message")
    return parsed
