"""
Model Context Protocol (MCP) server for Orion performance regression analysis.

This module provides tools for running performance regression analysis using
the cloud-bulldozer/orion library.

Tools, resources and prompts live under ``components/`` (one tool per module)
and are discovered automatically by FastMCP's ``FileSystemProvider``.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.providers import FileSystemProvider

from utils.constants import (
    DEFAULT_MCP_TRANSPORT,
    MCP_SERVER_HOST,
    MCP_SERVER_NAME,
    MCP_SERVER_PORT,
)

logger = logging.getLogger(__name__)

COMPONENTS_DIR = Path(__file__).resolve().parent / "components"

mcp = FastMCP(name=MCP_SERVER_NAME, providers=[FileSystemProvider(COMPONENTS_DIR)])


def main() -> None:
    """Parse CLI arguments and run the MCP server."""
    parser = argparse.ArgumentParser(description="Orion MCP Server")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level (default: INFO)",
    )
    args = parser.parse_args()

    log_level = getattr(logging, args.log_level)
    logging.basicConfig(level=log_level, format="%(levelname)s:     %(message)s", force=True)

    if os.getenv("ES_SERVER") is None:
        logger.error("ES_SERVER environment variable is not set")
        sys.exit(1)
    transport = os.getenv("MCP_TRANSPORT", DEFAULT_MCP_TRANSPORT)
    logger.info("Running MCP server with transport: %s", transport)
    if transport == "stdio":
        mcp.run(transport=transport, log_level=args.log_level)
    else:
        mcp.run(transport=transport, host=MCP_SERVER_HOST, port=MCP_SERVER_PORT,
                log_level=args.log_level)


if __name__ == "__main__":
    main()
