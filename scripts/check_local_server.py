"""Check a local speech server against one of the OpenAI-compatible modules.

Runs a fixture ("The sky is blue, validate.") through a real ``ASREngine`` whose
module points at the server, and prints every interim and final with its time
since the audio started. The audio starts only once the module is connected, so a
server that loads its model on the first request does not lose the first words.

The recipes behind docs/local-speech-recognition.md, each with its server already
running (see that page for the commands that start them):

    # mlx-audio server, batch
    uv run python scripts/check_local_server.py transcriptions \\
        --url http://localhost:8765/v1/audio/transcriptions \\
        --model mlx-community/parakeet-tdt-0.6b-v3

    # whisper.cpp server (it serves one model and ignores the name)
    uv run python scripts/check_local_server.py transcriptions \\
        --url http://127.0.0.1:8081/v1/audio/transcriptions --model whisper-1

    # speaches (faster-whisper)
    uv run python scripts/check_local_server.py transcriptions \\
        --url http://127.0.0.1:8082/v1/audio/transcriptions \\
        --model Systran/faster-whisper-small --language en

    # mlx-audio server, streaming
    uv run python scripts/check_local_server.py realtime \\
        --url 'ws://localhost:8765/v1/realtime?model=mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit' \\
        --model mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit

Exits 0 when a final contains "validate", 1 otherwise. Needs the
``openai-compatible`` and ``silero`` extras (both in the dev environment).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from asr_engine import ASREngine, ASREngineConfig, ScriptableAudioSource
from asr_engine.audio import AudioFormat
from asr_engine.modules.base import SpeechUtterance

FIXTURES = Path(__file__).resolve().parent.parent / "tests-e2e" / "fixtures"
# Each protocol's sample rate: batch models take 16 kHz, the Realtime API 24 kHz.
RATES = {"transcriptions": 16000, "realtime": 24000}
EXPECTED_WORD = "validate"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("protocol", choices=sorted(RATES))
    p.add_argument("--url", required=True, help="the server's full endpoint URL")
    p.add_argument("--model", required=True, help="the server's model name")
    p.add_argument("--language", default=None, help="e.g. en; unset = server decides")
    p.add_argument(
        "--interim-interval",
        type=float,
        default=0.5,
        help="transcriptions: seconds of audio between interims; 0 turns them off",
    )
    p.add_argument(
        "--turn-detection",
        default="true",
        help="realtime: true, false, or a JSON object (false needs --vad)",
    )
    p.add_argument(
        "--vad",
        action="store_true",
        help="realtime: put the Silero gate in front (transcriptions always has it)",
    )
    p.add_argument(
        "--connect-timeout", type=float, default=120.0, help="seconds to wait for it"
    )
    p.add_argument(
        "--wait", type=float, default=8.0, help="seconds to wait for the final"
    )
    p.add_argument("--debug", action="store_true", help="log the module at DEBUG")
    return p.parse_args()


def engine_config(args: argparse.Namespace) -> dict[str, Any]:
    module: dict[str, Any] = {"url": args.url, "model": args.model}
    if args.language:
        module["language"] = args.language
    gated = True
    if args.protocol == "transcriptions":
        module["type"] = "openai-transcriptions-compatible"
        module["interim_interval_s"] = args.interim_interval
    else:
        module["type"] = "openai-realtime-compatible"
        module["turn_detection"] = json.loads(args.turn_detection)
        gated = args.vad
    config: dict[str, Any] = {
        "audio": {"sample_rate": RATES[args.protocol]},
        "sound_feedback": {"enabled": False},
        "module": module,
    }
    if gated:
        config["vad"] = {"type": "silero"}
    return config


async def run(args: argparse.Namespace) -> int:
    rate = RATES[args.protocol]
    fixture = FIXTURES / f"sample_{rate}_theskyisbluevalidate.wav"
    finals: list[str] = []
    started = 0.0

    async def on_utterance(utterance: SpeechUtterance) -> None:
        kind = "FINAL  " if utterance.is_final else "interim"
        print(f"{time.monotonic() - started:6.2f}s {kind} {utterance.transcript!r}")
        if utterance.is_final:
            finals.append(utterance.transcript)

    source = ScriptableAudioSource(AudioFormat(sample_rate=rate))
    engine = ASREngine(
        ASREngineConfig.from_dict(engine_config(args)),
        audio_source=source,
        on_speech_utterance=on_utterance,
    )
    await engine.start()
    try:
        connecting = time.monotonic()
        while not engine.status()["connected"]:
            if time.monotonic() - connecting > args.connect_timeout:
                print(f"not connected after {args.connect_timeout:.0f}s; see the log")
                return 1
            await asyncio.sleep(0.05)
        print(f"connected after {time.monotonic() - connecting:.2f}s")

        started = time.monotonic()
        await source.play(fixture, trailing_silence_s=1.0)
        deadline = time.monotonic() + args.wait
        while time.monotonic() < deadline:
            if any(EXPECTED_WORD in final.lower() for final in finals):
                break
            await asyncio.sleep(0.05)
    finally:
        await engine.stop()

    ok = any(EXPECTED_WORD in final.lower() for final in finals)
    print("OK" if ok else f"FAILED: no final containing {EXPECTED_WORD!r}")
    return 0 if ok else 1


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    for noisy in ("websockets", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
