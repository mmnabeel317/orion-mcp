"""
Shared helpers for orion-mcp tools.

Holds the reusable parameter annotations and the small parsing/setup helpers
that several tools in ``components/tools/`` rely on. Kept outside ``server.py``
so tool modules can import them without importing the server (which loads the
tool modules itself).
"""

import json
import logging
import os
from typing import Annotated

from pydantic import Field

from utils.constants import DEFAULT_CONFIG, ORION_CONFIGS_PATH
from utils.header_decryption import get_es_config_from_headers
from utils.utils import current_es_config

logger = logging.getLogger(__name__)

# TLS verification — disable only for private CAs (set ORION_VERIFY_TLS=false)
VERIFY_TLS = os.getenv("ORION_VERIFY_TLS", "true").lower() != "false"

# Common parameter types — define once, reuse across all tools
VersionParam = Annotated[str, Field(description="OpenShift version (e.g. '4.22', '5.0')")]
LookbackParam = Annotated[str, Field(description="Number of days to lookback")]
ConfigParam = Annotated[str | None, Field(
    description="Orion configuration file name (e.g. 'cluster-density.yaml'). For regression tools, supports comma-separated list (e.g. 'cluster-density.yaml,node-density.yaml').",
)]
InputVarsParam = Annotated[str, Field(
    description="JSON string of template variables for the config (e.g. platform, workerNodesCount, clusterType, fips, ipsec, encrypted, networkType, masterNodesType, masterNodesCount, workerNodesType, jobtype).",
)]


def parse_input_vars(input_vars: str) -> dict | None:
    """Parse a JSON input_vars string into a dict, or return None if empty.

    Raises ValueError on malformed JSON so callers can distinguish
    "not provided" (None) from "provided but broken".
    """
    if not input_vars:
        return None
    try:
        return json.loads(input_vars)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"Malformed input_vars JSON: {exc}") from exc


def split_configs(config_name: str | None, default: list[str] | None = None) -> list[str]:
    """Split a comma-separated config_name into a list. Returns default (or [DEFAULT_CONFIG]) if empty."""
    if not config_name:
        return default if default is not None else [DEFAULT_CONFIG]
    return [c.strip() for c in config_name.split(",") if c.strip()]


def config_path(config_name: str) -> str:
    """Return the full filesystem path for an Orion config filename."""
    return os.path.join(ORION_CONFIGS_PATH, config_name)


def orion_error_snippet(result) -> str:
    """Extract a short error message from an Orion subprocess result."""
    return (result.stderr or result.stdout or "")[:200].strip()


def extract_and_set_es_server(ctx) -> None:
    """
    Extract ES config from request headers and set in context variable.

    If encrypted ES config found in headers, decrypts it and sets current_es_config
    context variable. Config includes: es_server, es_metadata_index, es_benchmark_index.
    Downstream code (get_data_source, get_es_metadata_index, get_es_benchmark_index)
    checks context first.

    Falls back to environment variables if no header present.
    """
    if not ctx:
        return

    # Access HTTP headers through ctx.request_context.request.headers (Starlette Request)
    try:
        if hasattr(ctx, 'request_context') and ctx.request_context:
            request = ctx.request_context.request
            if request and hasattr(request, 'headers'):
                # Convert Starlette Headers to dict
                headers_dict = dict(request.headers)
                es_config = get_es_config_from_headers(headers_dict)
                if es_config:
                    current_es_config.set(es_config)
    except Exception as exc:
        logger.debug("Failed to extract ES config from headers, falling back to env vars: %s", exc)


async def resolve_config_and_vars(
    ctx,
    config_name: str | None,
    _version: str,
    input_vars: str = "",
) -> tuple[str, dict | None]:
    """Common setup for tools: extract ES config, parse config name and input_vars.

    Returns (config_value, iv).
    """
    extract_and_set_es_server(ctx)
    config_value = config_name or DEFAULT_CONFIG
    iv = parse_input_vars(input_vars) if input_vars else None
    return config_value, iv
