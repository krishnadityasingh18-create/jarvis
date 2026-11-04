import asyncio
import dataclasses
import inspect
from pathlib import Path

import pytest

from jarvis import db
from jarvis.config import load_config


@pytest.fixture
def cfg(tmp_path):
    c = load_config(Path("config.toml"))
    c.routes = {n: dataclasses.replace(r) for n, r in c.routes.items()}
    c.db_path = tmp_path / "j.db"
    c.briefing_enabled = False   # the app clock must not start briefings during tests
    return c


@pytest.fixture(autouse=True)
def no_weather_network(monkeypatch):
    """The island's glance asks for the weather; tests never reach the network."""
    from jarvis.tools import briefing
    monkeypatch.setattr(briefing, "weather_short", lambda cfg: ("31°", "Clear"))
    monkeypatch.setattr("jarvis.artifacts._weather_cache", {})   # each test asks afresh


@pytest.fixture(autouse=True)
def not_fullscreen(monkeypatch):
    """Whatever the dev is doing on this laptop must not leak `fullscreen` messages into tests."""
    from jarvis import fullscreen
    monkeypatch.setattr(fullscreen, "_query", lambda: 1)


@pytest.fixture
def conn(cfg):
    return db.connect(cfg.db_path)


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        args = {k: pyfuncitem.funcargs[k] for k in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(pyfuncitem.obj(**args))
        return True
