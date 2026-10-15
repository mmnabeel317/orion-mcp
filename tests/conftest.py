"""Shared pytest fixtures for orion-mcp tests."""

import pytest

from tests.helpers import FakeOrion
from utils.utils import current_es_config


@pytest.fixture
def fake_orion(monkeypatch):
    """Return a factory that patches ``run_orion`` in a tool module with a FakeOrion.

    Usage: ``fake = fake_orion(regression_module, make_result([...]))``.
    """

    def _install(module, results):
        fake = FakeOrion(results)
        monkeypatch.setattr(module, "run_orion", fake)
        return fake

    return _install


@pytest.fixture(autouse=True)
def reset_es_context(monkeypatch):
    """Isolate tests from the ES context variable and ES_SERVER env var."""
    token = current_es_config.set(None)
    monkeypatch.setenv("ES_SERVER", "http://es.example.com:9200")
    yield
    current_es_config.reset(token)
