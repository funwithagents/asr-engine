"""Probe Deepgram Flux (Listen v2) end-of-turn behaviour for VAD gating.

specs/vad.md rests on two facts about Flux that only a live run can show:

- **(a) EndOfTurn latency on real silence** — send a fixture, then real-time
  silence, and log when ``EndOfTurn`` arrives after the speech ends. This sizes
  the gate hangover (``min_silence_duration_ms``) that lets Flux end the turn
  itself.
- **(b) A silence burst ends an open turn** — send a fixture plus a short
  hangover, stop sending (what a closed gate does), then send ``--burst`` seconds
  of silence in one unpaced message. Does ``EndOfTurn`` arrive, and how fast?

Usage (the key lives in ~/.zshrc, so go through an interactive zsh):
    zsh -ic 'uv run python scripts/probe_deepgram_flux_silence.py'               # (a)
    zsh -ic 'uv run python scripts/probe_deepgram_flux_silence.py \
        --hangover 0.3 --burst 2.5'                                              # (b)
    zsh -ic 'uv run python scripts/probe_deepgram_flux_silence.py \
        --hangover 0.3 --burst 0'       # control: stop sending, no burst

Reads the API key from the env var named by ``--api-key-env`` (default
DEEPGRAM_API_KEY) and never prints it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v2.types.listen_v2turn_info import ListenV2TurnInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIO = REPO_ROOT / "tests-e2e/fixtures/sample_44100_theskyisblue.mp3"
CHUNK_S = 0.1


def load_pcm(path: Path) -> tuple[bytes, int, float]:
    """The file as s16 mono bytes, its rate, and when its speech ends (seconds)."""
    data, rate = sf.read(path, dtype="int16", always_2d=True)
    mono = data[:, 0]
    loud = np.nonzero(np.abs(mono) > 0.02 * 32768)[0]
    speech_end_s = (loud[-1] / rate) if len(loud) else len(mono) / rate
    return mono.astype("<i2").tobytes(), int(rate), float(speech_end_s)


async def run(args: argparse.Namespace) -> None:
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        sys.exit(f"${args.api_key_env} is not set (source ~/.zshrc: run via `zsh -ic`)")

    pcm, rate, speech_end_s = load_pcm(args.audio)
    print(
        f"audio: {args.audio.name} — {rate} Hz, {len(pcm) / 2 / rate:.2f}s, "
        f"speech ends at {speech_end_s:.2f}s"
    )
    t0: float | None = None
    end_of_turn_at: list[float] = []

    def wall() -> float:
        return 0.0 if t0 is None else time.monotonic() - t0

    def log(kind: str, detail: str = "") -> None:
        print(f"[wall {wall():6.2f}s] {kind} {detail}", flush=True)

    client = AsyncDeepgramClient(api_key=api_key)
    async with client.listen.v2.connect(
        model=args.model,
        encoding="linear16",
        sample_rate=str(rate),
        eot_threshold=str(args.eot_threshold),
        eot_timeout_ms=str(args.eot_timeout_ms),
    ) as conn:

        async def on_message(msg: object) -> None:
            if not isinstance(msg, ListenV2TurnInfo):
                return
            if msg.event == "EndOfTurn":
                end_of_turn_at.append(wall())
            log(
                msg.event,
                f"turn={msg.turn_index:.0f} audio_end={msg.audio_window_end:.2f} "
                f"eot_conf={msg.end_of_turn_confidence:.2f} {msg.transcript!r}",
            )

        conn.on(EventType.MESSAGE, on_message)
        conn.on(EventType.ERROR, lambda e: log("ERROR", str(e)))
        listener = asyncio.create_task(conn.start_listening())

        async def send_paced(data: bytes) -> None:
            nonlocal t0
            chunk_bytes = int(rate * CHUNK_S) * 2
            sent = 0.0
            start = time.monotonic()
            for i in range(0, len(data), chunk_bytes):
                chunk = data[i : i + chunk_bytes]
                if t0 is None:
                    t0 = start
                await conn.send_media(chunk)
                sent += len(chunk) / 2 / rate
                await asyncio.sleep(max(0.0, start + sent - time.monotonic()))

        silence = bytes(int(rate * args.hangover) * 2)
        await send_paced(pcm + silence)
        log("SENT", f"speech + {args.hangover:.2f}s of real-time silence")
        if args.burst is not None:
            await asyncio.sleep(args.gap)
            if args.burst > 0:
                log("BURST", f"{args.burst:.2f}s of silence, unpaced")
                await conn.send_media(bytes(int(rate * args.burst) * 2))
            else:
                log("STOPPED", "sending nothing more")
        await asyncio.sleep(args.wait)
        listener.cancel()

    print("\n=== summary ===")
    if end_of_turn_at:
        print(
            f"EndOfTurn {end_of_turn_at[0] - speech_end_s:.2f}s after the speech ended"
            f" (wall {end_of_turn_at[0]:.2f}s)"
        )
    else:
        print("no EndOfTurn")


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--model", default="flux-general-en")
    parser.add_argument("--api-key-env", default="DEEPGRAM_API_KEY")
    parser.add_argument("--eot-threshold", type=float, default=0.7)
    parser.add_argument("--eot-timeout-ms", type=int, default=2000)
    parser.add_argument(
        "--hangover",
        type=float,
        default=6.0,
        help="seconds of real-time silence sent after the speech",
    )
    parser.add_argument(
        "--burst",
        type=float,
        default=None,
        help="then stop, and send this many seconds of silence in one message "
        "(0: send nothing)",
    )
    parser.add_argument(
        "--gap", type=float, default=0.0, help="seconds to wait before the burst"
    )
    parser.add_argument(
        "--wait", type=float, default=5.0, help="seconds to keep listening at the end"
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
