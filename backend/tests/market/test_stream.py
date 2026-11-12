"""Tests for the SSE generator and router."""

import json

from app.market.cache import PriceCache
from app.market.stream import create_stream_router, generate_events


class FakeRequest:
    """Stands in for starlette's Request: disconnects after `polls` checks."""

    client = None

    def __init__(self, polls: int, on_poll=None):
        self.polls = polls
        self.n = 0
        self.on_poll = on_poll

    async def is_disconnected(self) -> bool:
        self.n += 1
        if self.on_poll:
            self.on_poll(self.n)
        return self.n > self.polls


def _data_frames(frames):
    return [json.loads(f[len("data: ") :]) for f in frames if f.startswith("data: ")]


async def _collect(cache, req, **kw):
    return [f async for f in generate_events(cache, req, interval=0, **kw)]


async def test_retry_then_one_frame_when_idle():
    c = PriceCache()
    c.update("AAPL", 190.0, session_open=189.0)
    frames = await _collect(c, FakeRequest(4), keepalive=999)
    assert frames[0] == "retry: 1000\n\n"
    data = _data_frames(frames)
    assert len(data) == 1  # version unchanged afterwards
    assert data[0]["AAPL"]["price"] == 190.0
    assert data[0]["AAPL"]["day_change_percent"] == 0.5291


async def test_new_frame_on_each_change():
    c = PriceCache()
    c.update("AAPL", 190.0)
    frames = await _collect(
        c, FakeRequest(4, on_poll=lambda n: c.update("AAPL", 190.0 + n)), keepalive=999
    )
    assert len(_data_frames(frames)) == 4


async def test_empty_map_sent_after_last_ticker_removed():
    c = PriceCache()
    c.update("AAPL", 190.0)
    frames = await _collect(
        c, FakeRequest(3, on_poll=lambda n: n == 2 and c.remove("AAPL")), keepalive=999
    )
    data = _data_frames(frames)
    assert data[0].keys() == {"AAPL"}
    assert data[-1] == {}


async def test_keepalive_ping_when_quiet():
    c = PriceCache()
    c.update("AAPL", 190.0)
    frames = await _collect(c, FakeRequest(3), keepalive=0)  # every idle poll is "too quiet"
    assert ": ping\n\n" in frames
    assert len(_data_frames(frames)) == 1


async def test_payload_is_compact_json():
    c = PriceCache()
    c.update("AAPL", 190.0)
    frames = await _collect(c, FakeRequest(1))
    frame = next(f for f in frames if f.startswith("data: "))
    assert frame.endswith("\n\n") and ", " not in frame


def test_router_factory_creates_independent_routers():
    r1, r2 = create_stream_router(PriceCache()), create_stream_router(PriceCache())
    assert r1 is not r2
    assert [r.path for r in r2.routes] == ["/api/stream/prices"]


def _endpoint(router):
    return next(r.endpoint for r in router.routes if r.path == "/api/stream/prices")


class RouteRequest(FakeRequest):
    """FakeRequest plus the .app.state the route reads when no cache is injected."""

    def __init__(self, polls: int, state=None):
        super().__init__(polls)
        self.app = type("App", (), {"state": state})()


async def _first_frames(response, n: int = 2) -> list[str]:
    # The SSE body never ends on its own, so read only the first n chunks.
    # (httpx's ASGITransport buffers whole responses and would hang here.)
    frames = []
    async for chunk in response.body_iterator:
        frames.append(chunk)
        if len(frames) == n:
            break
    await response.body_iterator.aclose()
    return frames


async def test_route_streams_with_sse_headers():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    response = await _endpoint(create_stream_router(cache, interval=0))(RouteRequest(5))
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    retry, data = await _first_frames(response)
    assert retry == "retry: 1000\n\n"
    assert json.loads(data[6:])["AAPL"]["ticker"] == "AAPL"


async def test_route_reads_cache_from_app_state():
    cache = PriceCache()
    cache.update("MSFT", 420.0)
    state = type("State", (), {"price_cache": cache})()
    response = await _endpoint(create_stream_router(interval=0))(RouteRequest(5, state))
    _, data = await _first_frames(response)
    assert "MSFT" in json.loads(data[6:])
