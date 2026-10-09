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
    return c


@pytest.fixture
def conn(cfg):
    return db.connect(cfg.db_path)


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        args = {k: pyfuncitem.funcargs[k] for k in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(pyfuncitem.obj(**args))
        return True
