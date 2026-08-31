"""
Manual end-to-end test — MCP server + demo client.

Starts the ASR MCP server in a background process, waits for it to be ready,
then runs the demo client in the foreground so you can speak into the
microphone and watch interim / final results appear.

Usage:
    uv run python scripts/test_e2e_client.py --config config.json
    uv run python scripts/test_e2e_client.py --config config.json --port 8080
    uv run python scripts/test_e2e_client.py --config config.json --verbose

Press Ctrl+C to stop both the client and the server.

Prerequisites:
    - A valid config.json with a real Deepgram API key
    - A working microphone
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from threading import Thread


def _wait_for_server(url: str, timeout: float = 15.0) -> bool:
    """Poll the server URL until it responds (any HTTP status) or timeout is reached."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except urllib.error.HTTPError:
            # Any HTTP error (e.g. 406 Not Acceptable) means the server is up
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E test: MCP server + demo client")
    parser.add_argument(
        "--config",
        default="config.json",
        metavar="PATH",
        help="Path to the server config file (default: config.json)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override the port from the config file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Stream server logs to the terminal (mixed with client output)",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    # Read port from config (may be overridden by --port)
    try:
        config = json.loads(config_path.read_text())
        port = args.port or config.get("server", {}).get("port", 8000)
        host = config.get("server", {}).get("host", "0.0.0.0")
        # Resolve 0.0.0.0 to localhost for the client URL
        client_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    except Exception as exc:
        print(f"Error reading config: {exc}", file=sys.stderr)
        sys.exit(1)

    server_url = f"http://{client_host}:{port}/mcp"

    # ------------------------------------------------------------------ #
    # Start the MCP server in a background subprocess                     #
    # ------------------------------------------------------------------ #
    print(f"[e2e] Starting MCP server (config={config_path}, port={port})...")
    if args.verbose:
        # Let server output flow directly to the terminal
        server_proc = subprocess.Popen(
            ["uv", "run", "asr-engine-mcp", "--config", str(config_path)],
        )
        server_log_lines: list[str] = []
    else:
        # Capture server output silently; dump it only on failure
        server_proc = subprocess.Popen(
            ["uv", "run", "asr-engine-mcp", "--config", str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        server_log_lines = []

        def _collect_server_log():
            assert server_proc.stdout is not None
            for line in server_proc.stdout:
                server_log_lines.append(line)

        Thread(target=_collect_server_log, daemon=True).start()

    # Wait until the server is accepting HTTP connections
    print(f"[e2e] Waiting for server at {server_url} ...")
    if not _wait_for_server(server_url, timeout=20.0):
        print("[e2e] Server did not start in time.", file=sys.stderr)
        server_proc.terminate()
        server_proc.wait()
        if server_log_lines:
            print("\n[e2e] Server output:\n", file=sys.stderr)
            sys.stderr.writelines(server_log_lines)
        else:
            print("[e2e] Re-run with --verbose to see server logs.", file=sys.stderr)
        sys.exit(1)

    print("[e2e] Server is up. Starting demo client — speak into your microphone.")
    print("[e2e] Press Ctrl+C to stop.\n")

    # ------------------------------------------------------------------ #
    # Run the demo client in the foreground                               #
    # ------------------------------------------------------------------ #
    client_proc = subprocess.Popen(
        ["uv", "run", "asr-mcp-client", "--server", server_url],
    )

    def _shutdown(sig, frame):
        print("\n[e2e] Shutting down...")
        client_proc.terminate()
        server_proc.terminate()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    client_proc.wait()
    server_proc.terminate()
    server_proc.wait()
    print("[e2e] Done.")


if __name__ == "__main__":
    main()
