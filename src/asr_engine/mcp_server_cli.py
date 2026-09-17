import argparse
import asyncio
import logging
import sys

from asr_engine._logging import setup_logging
from asr_engine.config import MCPServerConfig

_MCP_EXTRA_MODULES = {"mcp", "uvicorn"}
_MCP_EXTRA_HINT = "asr-engine-mcp requires the mcp extra: pip install 'asr-engine[mcp]'"

_LOG_LEVELS = sorted(
    name for name in logging.getLevelNamesMapping() if name != "NOTSET"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="ASR Engine MCP Server")
    parser.add_argument(
        "--config",
        default="config.json",
        metavar="PATH",
        help="Path to the JSON config file (default: config.json)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=_LOG_LEVELS,
        metavar="LEVEL",
        help=f"Logging level (default: INFO). One of: {', '.join(_LOG_LEVELS)}.",
    )
    args = parser.parse_args()

    # The console script is installed even without the mcp extra, so the MCP
    # stack is imported here: a missing extra becomes an install hint, not a
    # traceback. Only a missing top-level package means the extra is absent; a
    # missing submodule (e.g. an incompatible mcp version) or any other import
    # error is a real problem and is re-raised.
    try:
        from asr_engine.server import run_server
    except ModuleNotFoundError as exc:
        if exc.name not in _MCP_EXTRA_MODULES:
            raise
        raise SystemExit(_MCP_EXTRA_HINT) from exc

    setup_logging(args.log_level)

    try:
        config = MCPServerConfig.from_json_file(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(run_server(config, log_level=args.log_level))
    except (ValueError, ImportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
