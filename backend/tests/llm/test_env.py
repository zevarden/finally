"""Env handling for Docker: quoted values from --env-file, and localhost api_base rewrite."""

import logging

import pytest

from app.llm import client

MSGS = [{"role": "user", "content": "hi"}]


# --------------------------------------------------------------------------- quotes

@pytest.mark.parametrize("raw,expected", [
    ('"http://localhost:4000"', "http://localhost:4000"),
    ("'http://localhost:4000'", "http://localhost:4000"),
    ('  "http://proxy:4000"  ', "http://proxy:4000"),
    ('""', None),
    ('"mismatched\'', "\"mismatched'"),
    ('""double""', '"double"'),            # only one layer is stripped
    ("plain", "plain"),
    ('"', '"'),
])
def test_env_strips_one_layer_of_quotes(monkeypatch, raw, expected):
    monkeypatch.setenv("LITELLM_API_BASE", raw)
    assert client._env("LITELLM_API_BASE") == expected


async def test_quoted_values_reach_litellm_unquoted(fake_llm, monkeypatch):
    monkeypatch.setenv("LITELLM_API_BASE", '"http://proxy:4000"')
    monkeypatch.setenv("LITELLM_API_KEY", '"sk-virtual"')
    monkeypatch.setenv("LLM_MODEL", "'xai/grok-4.7'")
    fake_llm.queue({"message": "hello"})
    await client.request_structured_response(MSGS)
    call = fake_llm.calls[0]
    assert call["api_base"] == "http://proxy:4000"
    assert call["api_key"] == "sk-virtual"
    assert call["model"] == "litellm_proxy/xai/grok-4.7"


async def test_quoted_master_key_direct_mode(fake_llm, monkeypatch):
    monkeypatch.setenv("LITELLM_MASTER_KEY", '"sk-master"')
    fake_llm.queue({"message": "hello"})
    await client.request_structured_response(MSGS)
    assert fake_llm.calls[0]["api_key"] == "sk-master"


def test_quoted_mock_flag(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", '"true"')
    assert client.is_mock_mode() is True


# --------------------------------------------------------------------------- docker

@pytest.fixture
def docker(monkeypatch):
    monkeypatch.setenv("FINALLY_IN_DOCKER", "1")
    client._logged_rewrites.clear()


def test_docker_detection(monkeypatch, tmp_path):
    assert client.in_docker() is False
    marker = tmp_path / ".dockerenv"
    marker.touch()
    monkeypatch.setattr(client, "DOCKERENV_PATH", str(marker))
    assert client.in_docker() is True
    monkeypatch.setenv("FINALLY_IN_DOCKER", "0")       # explicit override wins
    assert client.in_docker() is False
    monkeypatch.setenv("FINALLY_IN_DOCKER", "1")
    monkeypatch.setattr(client, "DOCKERENV_PATH", str(tmp_path / "missing"))
    assert client.in_docker() is True


@pytest.mark.parametrize("base,expected", [
    ("http://localhost:4000", "http://host.docker.internal:4000"),
    ("http://127.0.0.1:4000/v1", "http://host.docker.internal:4000/v1"),
    ("https://LOCALHOST", "https://host.docker.internal"),
    ("http://[::1]:4000/", "http://host.docker.internal:4000/"),
    ("http://user:pw@localhost:4000/x?y=1", "http://user:pw@host.docker.internal:4000/x?y=1"),
    ("http://proxy.example.com:4000", "http://proxy.example.com:4000"),
    ("http://host.docker.internal:4000", "http://host.docker.internal:4000"),
    ("http://10.0.0.5:4000", "http://10.0.0.5:4000"),
])
def test_rewrite_in_docker(docker, base, expected):
    assert client.resolve_api_base(base) == expected


def test_no_rewrite_outside_docker():
    assert client.resolve_api_base("http://localhost:4000") == "http://localhost:4000"


async def test_quoted_localhost_base_in_docker(fake_llm, monkeypatch, docker):
    # The exact failing setup from the bug report.
    monkeypatch.setenv("LITELLM_API_BASE", '"http://localhost:4000"')
    fake_llm.queue({"message": "hello"})
    await client.request_structured_response(MSGS)
    assert fake_llm.calls[0]["api_base"] == "http://host.docker.internal:4000"


def test_rewrite_logged_once_without_credentials(docker, caplog):
    with caplog.at_level(logging.INFO, logger="app.llm.client"):
        for _ in range(3):
            client.resolve_api_base("http://user:secret@localhost:4000")
    lines = [r.getMessage() for r in caplog.records if "host.docker.internal" in r.getMessage()]
    assert len(lines) == 1
    assert "secret" not in lines[0] and "user" not in lines[0]
    assert "http://host.docker.internal:4000" in lines[0]
