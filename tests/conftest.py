"""
Shared pytest configuration and fixtures for the orion-mcp unit tests.

The tests must run without network access, credentials, or an Orion
subprocess. This module enforces that:

* ``orion_mcp`` calls ``list_orion_configs()`` (a GitHub API request) at
  import time, so it is imported here with that function patched out.
* Real HTTP transports and asyncio subprocess creation are replaced with
  functions that raise for every test. Tests that need HTTP responses use
  ``httpx.MockTransport`` (see the ``mock_async_http`` fixture).
* Environment variables and the ES config context variable are reset
  around every test so tests cannot leak state into each other.
"""

import asyncio
import base64
import importlib
import json
import os
import subprocess
from unittest import mock

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import utils.utils as orion_utils

FAKE_ORION_CONFIGS = ["cluster-density.yaml", "node-density.yaml"]

ENV_VARS_TO_CLEAR = (
    "ES_SERVER",
    "es_metadata_index",
    "ES_METADATA_INDEX",
    "es_benchmark_index",
    "ES_BENCHMARK_INDEX",
    "HEADER_SYMMETRIC_KEY",
)

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _no_network(*_args, **_kwargs):
    raise RuntimeError("Real network access is disabled in unit tests")


async def _async_no_network(*_args, **_kwargs):
    raise RuntimeError("Real network access is disabled in unit tests")


async def _async_no_subprocess(*_args, **_kwargs):
    raise RuntimeError("Spawning subprocesses is disabled in unit tests")


def _import_orion_mcp():
    """Import orion_mcp without triggering the import-time GitHub request."""
    with mock.patch.object(
        orion_utils, "list_orion_configs", return_value=list(FAKE_ORION_CONFIGS)
    ), mock.patch.object(httpx.HTTPTransport, "handle_request", _no_network):
        return importlib.import_module("orion_mcp")


orion_mcp = _import_orion_mcp()


@pytest.fixture(autouse=True)
def isolate_externals(monkeypatch):
    """Block real network/subprocess access and reset env + context state."""
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _no_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _async_no_network)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _async_no_subprocess)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _async_no_subprocess)

    for var in ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(var, raising=False)

    token = orion_utils.current_es_config.set(None)
    yield
    orion_utils.current_es_config.reset(token)


@pytest.fixture
def es_server(monkeypatch):
    """Set a fake ES_SERVER environment variable and return its value."""
    url = "http://es.test:9200"
    monkeypatch.setenv("ES_SERVER", url)
    return url


@pytest.fixture
def make_result():
    """Factory for subprocess.CompletedProcess objects mimicking Orion runs."""

    def _make(stdout="", stderr="", returncode=0, args=None):
        if not isinstance(stdout, str):
            stdout = json.dumps(stdout)
        return subprocess.CompletedProcess(
            args=args or ["orion"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    return _make


@pytest.fixture
def run_orion_mock(monkeypatch):
    """Replace orion_mcp.run_orion with an AsyncMock (default: Orion 'no data' exit code 3)."""
    no_data = subprocess.CompletedProcess(args=["orion"], returncode=3, stdout="[]", stderr="")
    runner = mock.AsyncMock(return_value=no_data)
    monkeypatch.setattr(orion_mcp, "run_orion", runner)
    return runner


@pytest.fixture
def mock_async_http(monkeypatch):
    """Route every httpx.AsyncClient through an httpx.MockTransport.

    Call the returned function with a request handler
    ``handler(request) -> httpx.Response``.
    """

    def _install(handler):
        transport = httpx.MockTransport(handler)

        def _client_factory(*args, **kwargs):
            kwargs.pop("transport", None)
            return _REAL_ASYNC_CLIENT(*args, transport=transport, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _client_factory)
        return transport

    return _install


@pytest.fixture
def symmetric_key(monkeypatch):
    """Generate an AES-256 key, export it as HEADER_SYMMETRIC_KEY, return raw bytes."""
    key = os.urandom(32)
    monkeypatch.setenv("HEADER_SYMMETRIC_KEY", base64.b64encode(key).decode())
    return key


@pytest.fixture
def encrypt():
    """Return a helper that encrypts a payload the way clients (e.g. BugZooka) do."""

    def _encrypt(payload, key):
        if not isinstance(payload, (str, bytes)):
            payload = json.dumps(payload)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(nonce, payload, associated_data=None)
        return base64.b64encode(nonce + ciphertext).decode()

    return _encrypt
