import argparse
import asyncio
import logging
import sys

from asr_engine._logging import setup_logging
from asr_engine.config import load_config
from asr_engine.server import run_server

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

    setup_logging(args.log_level)

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(run_server(config, log_level=args.log_level))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
