"""LiteLLM wrapper: one structured-output chat call, with a single retry on bad output.

Configuration (read per call, so tests and restarts pick up changes):
    LITELLM_API_KEY     LiteLLM proxy virtual key; preferred for proxy calls.
    LITELLM_MASTER_KEY  Fallback proxy key when LITELLM_API_KEY is empty, and the key
                        sent to xAI directly when LITELLM_API_BASE is not set.
    LITELLM_API_BASE    Optional LiteLLM proxy URL (e.g. http://localhost:4000). When set,
                        the request goes through the proxy's OpenAI-compatible route.
    LLM_MODEL           Optional model override (default "xai/grok-4.7").
    LLM_MOCK            "true" -> deterministic mock responses (see mock.py); no network.
    FINALLY_IN_DOCKER   Optional "1"/"0" to force/deny container detection (default:
                        /.dockerenv exists). In a container, a localhost/127.0.0.1/::1
                        LITELLM_API_BASE is rewritten to host.docker.internal.

All values are stripped and one layer of matching surrounding quotes is removed
(`docker --env-file` passes KEY="v" through with the quotes).
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .models import RESPONSE_FORMAT, LLMResponse, ParseError, parse_llm_response

logger = logging.getLogger(__name__)

# The model id is hyphenated: "grok4.7" (no hyphen) is rejected by the proxy.
DEFAULT_MODEL = "xai/grok-4.7"
REQUEST_TIMEOUT_S = 60.0

FALLBACK_MESSAGE = (
    "Sorry, I had trouble putting together a proper response just now, so I didn't make "
    "any changes. Could you try asking again?"
)


class LLMUnavailableError(RuntimeError):
    """The LLM call failed outright (network, auth, provider error)."""


DOCKERENV_PATH = "/.dockerenv"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
DOCKER_HOST_ALIAS = "host.docker.internal"
_logged_rewrites: set[str] = set()


def _unquote(value: str) -> str:
    """Strip one layer of matching surrounding quotes. `docker --env-file` keeps them
    literally, so KEY="value" in .env arrives as '"value"'."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def _env(name: str) -> str | None:
    """Env var value: stripped, unquoted, and "" treated as unset (Docker passes "")."""
    return _unquote(os.environ.get(name, "").strip()) or None


def is_mock_mode() -> bool:
    return (_env("LLM_MOCK") or "").lower() in ("1", "true", "yes", "on")


def in_docker() -> bool:
    flag = (_env("FINALLY_IN_DOCKER") or "").lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return os.path.exists(DOCKERENV_PATH)


def resolve_api_base(api_base: str) -> str:
    """Inside a container, localhost means the container itself; point a loopback
    api_base at the Docker host instead (scheme, port, path and query are kept)."""
    if not in_docker():
        return api_base
    try:
        parts = urlsplit(api_base)
        host, port = parts.hostname, parts.port
    except ValueError:
        return api_base
    if host not in _LOOPBACK_HOSTS:
        return api_base
    userinfo = parts.netloc.rpartition("@")[0]
    netloc = (f"{userinfo}@" if userinfo else "") + DOCKER_HOST_ALIAS
    if port is not None:
        netloc += f":{port}"
    rewritten = urlunsplit(parts._replace(netloc=netloc))
    if rewritten not in _logged_rewrites:
        _logged_rewrites.add(rewritten)
        shown = f"{parts.scheme}://{DOCKER_HOST_ALIAS}" + (f":{port}" if port else "")
        logger.info(
            "Running in Docker: LITELLM_API_BASE points at %s; using %s instead",
            host, shown,
        )
    return rewritten


def _completion_kwargs() -> dict[str, Any]:
    model = _env("LLM_MODEL") or DEFAULT_MODEL
    api_base = _env("LITELLM_API_BASE")
    kwargs: dict[str, Any] = {"timeout": REQUEST_TIMEOUT_S}
    if api_base:
        # LiteLLM proxy: the proxy resolves the model name to a provider deployment.
        # The virtual key (LITELLM_API_KEY) is the one meant for calls.
        kwargs["model"] = f"litellm_proxy/{model}"
        kwargs["api_base"] = resolve_api_base(api_base)
        api_key = _env("LITELLM_API_KEY") or _env("LITELLM_MASTER_KEY")
    else:
        kwargs["model"] = model
        api_key = _env("LITELLM_MASTER_KEY")
    if api_key:
        kwargs["api_key"] = api_key
    return kwargs


async def _acompletion(**kwargs: Any) -> Any:
    """The single network seam (tests monkeypatch this)."""
    # Use the bundled model-cost map instead of fetching it from GitHub on import.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm  # imported lazily: slow to import, and unused in mock mode

    return await litellm.acompletion(**kwargs)


def _content_of(response: Any) -> str | None:
    try:
        return response.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError):
        try:
            return response["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError):
            return None


async def request_structured_response(messages: list[dict[str, str]]) -> LLMResponse:
    """Call the LLM and parse its JSON. Retries once on unparseable output, then returns
    a friendly no-action fallback. Raises LLMUnavailableError if the call itself fails."""
    kwargs = _completion_kwargs()
    for attempt in (1, 2):
        try:
            response = await _acompletion(
                messages=messages, response_format=RESPONSE_FORMAT, **kwargs
            )
        except Exception as exc:  # litellm raises many provider-specific types
            logger.warning("LLM call failed: %s: %s", type(exc).__name__, exc)
            raise LLMUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        try:
            return parse_llm_response(_content_of(response))
        except ParseError as exc:
            logger.warning("Unparseable LLM output (attempt %d): %s", attempt, exc)
    return LLMResponse(message=FALLBACK_MESSAGE)
