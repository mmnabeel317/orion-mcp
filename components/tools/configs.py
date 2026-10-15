"""get_orion_configs tool."""

from functools import lru_cache

from fastmcp.tools import tool

from utils.utils import list_orion_configs, orion_configs


@lru_cache(maxsize=1)
def available_configs() -> tuple[str, ...]:
    """Fetch the Orion config list once and cache it for the server lifetime.

    Fetched lazily on first use (rather than at import) so that loading the
    module — e.g. by FileSystemProvider or tests — does not hit the network.
    """
    return tuple(list_orion_configs())


@tool()
def get_orion_configs() -> list[str]:
    """List all available benchmark config files. Use when a user asks "what benchmarks exist", "list configs", or "what workloads can I test".

    Triggers: "what configs are available", "list benchmarks", "show all workloads".

    Returns:
        List of config filenames (e.g. ['cluster-density.yaml', 'node-density.yaml', ...]).
    """
    return orion_configs(list(available_configs()))
