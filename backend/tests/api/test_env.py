import sys

from app import main


def test_root_env_loaded_without_overriding(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FINALLY_TEST_NEW=from-file\nFINALLY_TEST_SET=from-file\n", encoding="utf-8")
    monkeypatch.setenv("FINALLY_TEST_SET", "from-env")
    monkeypatch.delenv("FINALLY_TEST_NEW", raising=False)
    monkeypatch.delitem(sys.modules, "pytest")  # simulate a normal (non-test) run
    try:
        assert main.load_root_env(env) is True
    finally:
        import pytest as _pytest

        sys.modules["pytest"] = _pytest
    import os

    assert os.environ["FINALLY_TEST_NEW"] == "from-file"
    assert os.environ["FINALLY_TEST_SET"] == "from-env"
    monkeypatch.delenv("FINALLY_TEST_NEW")


def test_root_env_skipped_under_pytest_and_when_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("FINALLY_TEST_SKIP=1\n", encoding="utf-8")
    assert main.load_root_env(env) is False
    assert main.load_root_env(tmp_path / "missing.env") is False


def test_root_env_path_is_repo_root():
    assert main.ROOT_ENV_FILE == main.BACKEND_DIR.parent / ".env"
