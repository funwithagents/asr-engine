"""
Manual test script — DeepgramV1Module or DeepgramV2Module + AudioCapture.

Usage:
    uv run python scripts/test_deepgram_live.py --api-key YOUR_KEY
    uv run python scripts/test_deepgram_live.py --api-key YOUR_KEY --version v1
    uv run python scripts/test_deepgram_live.py --api-key YOUR_KEY --version v2 --model flux-general-en
    uv run python scripts/test_deepgram_live.py --api-key YOUR_KEY --version v1 --model nova-3 --language fr
    uv run python scripts/test_deepgram_live.py --api-key YOUR_KEY --list-devices

Press Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from asr_mcp._logging import setup_logging

setup_logging()
log = logging.getLogger("test_deepgram_live")


async def run(version: str, config: dict, device: str | None) -> None:
    from asr_mcp.audio import AudioCapture
    from asr_mcp.modules.base import ASRResult

    if version == "v1":
        from asr_mcp.modules.deepgram_v1 import DeepgramV1Module

        module = DeepgramV1Module(config=config)
    else:
        from asr_mcp.modules.deepgram_v2 import DeepgramV2Module

        module = DeepgramV2Module(config=config)

    loop = asyncio.get_running_loop()
    capture = AudioCapture(device=device, loop=loop)

    async def on_result(result: ASRResult) -> None:
        tag = "[FINAL]  " if result.is_final else "[interim]"
        conf = (
            f"  conf={result.confidence:.2f}" if result.confidence is not None else ""
        )
        print(f"{tag} {result.transcript}{conf}")

    audio_queue = capture.start()
    log.info(
        "Audio capture started (device=%s, version=%s). Speak now — Ctrl+C to stop.",
        device or "default",
        version,
    )

    try:
        await module.start(audio_queue, on_result)
    except asyncio.CancelledError:
        pass
    finally:
        await module.stop()
        capture.stop()
        log.info("Stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Deepgram transcription test")
    parser.add_argument("--api-key", required=True, help="Deepgram API key")
    parser.add_argument(
        "--version",
        choices=["v1", "v2"],
        default="v2",
        help="API version: v1 (nova-3, multi-language) or v2 (flux, English). Default: v2",
    )
    parser.add_argument("--model", default=None, help="Model name (overrides default)")
    parser.add_argument("--language", default="multi", help="Language code (v1 only)")
    parser.add_argument("--device", default=None, help="Audio input device name")
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Print available audio input devices and exit",
    )
    args = parser.parse_args()

    if args.list_devices:
        from asr_mcp.audio import AudioCapture

        devices = AudioCapture.list_devices()
        if devices:
            print("Available input devices:")
            for d in devices:
                print(f"  - {d}")
        else:
            print("No input devices found.")
        sys.exit(0)

    if args.version == "v1":
        config = {
            "api_key": args.api_key,
            "model": args.model or "nova-3",
            "language": args.language,
        }
    else:
        config = {
            "api_key": args.api_key,
            "model": args.model or "flux-general-en",
        }

    async def _main():
        task = asyncio.create_task(run(args.version, config, args.device))
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop = asyncio.new_event_loop()
    main_task = None
    try:
        main_task = loop.create_task(_main())
        loop.run_until_complete(main_task)
    except KeyboardInterrupt:
        if main_task:
            main_task.cancel()
            loop.run_until_complete(main_task)
    finally:
        loop.close()


if __name__ == "__main__":
    main()
