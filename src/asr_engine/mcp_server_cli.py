import argparse
import asyncio
import sys

from asr_engine.config import load_config
from asr_engine.server import run_server


def main() -> None:
    parser = argparse.ArgumentParser(description="ASR Engine MCP Server")
    parser.add_argument(
        "--config",
        default="config.json",
        metavar="PATH",
        help="Path to the JSON config file (default: config.json)",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(run_server(config))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
