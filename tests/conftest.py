"""Fixture chung: cách ly cache và env cho mọi test."""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Mỗi test dùng cache.db riêng, không chạm cache thật của server."""
    monkeypatch.setenv("WEB_SEARCH_CACHE_DB", str(tmp_path / "cache.db"))
    yield


@pytest.fixture(autouse=True)
def no_api_keys(monkeypatch):
    """Xoá key thật để test luôn đi vào nhánh free, bất kể môi trường máy."""
    for var in ("SERPER_API_KEY", "TAVILY_API_KEY", "JINA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    yield
