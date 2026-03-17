import argparse
import asyncio
import sys

from asr_mcp.config import load_config, validate_asr_type
from asr_mcp.engine import ASREngine
from asr_mcp.modules import REGISTRY
from asr_mcp.server import run_server


def main() -> None:
    parser = argparse.ArgumentParser(description="ASR MCP Server")
    parser.add_argument(
        "--config",
        default="config.json",
        metavar="PATH",
        help="Path to the JSON config file (default: config.json)",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        validate_asr_type(config, REGISTRY)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"ASR MCP Server starting — "
        f"host={config.server.host} port={config.server.port} "
        f"asr={config.asr.type}"
    )

    module_class = REGISTRY[config.asr.type]
    module = module_class(config=config.asr.extra)

    async def _noop(result):
        pass

    engine = ASREngine(config.audio, module, _noop)

    asyncio.run(run_server(config, engine))
