"""Probe an OpenAI Realtime transcription endpoint before (and after) building the
``openai_realtime_compatible`` module (specs/modules/openai-realtime-compatible.md).

The public docs leave a few load-bearing facts unstated. This script measures them
against a live endpoint (hosted OpenAI by default, or any compatible server via
``--url``):

- **Session setup** — what ``session.created`` / ``session.updated`` carry, and
  whether a GA ``session.update`` (``session.type = "transcription"``) is accepted.
- **Sample rates** — run with ``--rate`` other than 24000 to see whether the server
  rejects it (the audio is resampled to that rate before sending).
- **Turn detection** — with server VAD on (default), when ``speech_stopped``,
  ``committed``, the deltas and ``completed`` arrive relative to the audio.
  ``--no-turn-detection`` commits manually right after the speech instead.
- **Delta semantics** — incremental or cumulative, per ``item_id``.
- **Commit edge cases** — ``--commit-empty`` commits an empty buffer, then a tiny
  one, and logs the server's errors.
- **Idle** — ``--idle N`` sends nothing for N seconds after the audio and reports
  whether the server closes the socket.

Usage (the key lives in ~/.zshrc, so go through an interactive zsh):
    zsh -ic 'uv run python scripts/probe_openai_realtime.py'
    zsh -ic 'uv run python scripts/probe_openai_realtime.py --no-turn-detection'
    zsh -ic 'uv run python scripts/probe_openai_realtime.py --rate 16000'
    zsh -ic 'uv run python scripts/probe_openai_realtime.py --commit-empty'

Reads the API key from the env var named by ``--api-key-env`` (default
OPENAI_API_KEY) and never prints it.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import websockets

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIO = REPO_ROOT / "tests-e2e/fixtures/sample_24000_theskyisbluevalidate.wav"
DEFAULT_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
CHUNK_S = 0.1

T0: float | None = None
AUDIO_SENT_S = 0.0


def log(kind: str, detail: str = "") -> None:
    wall = 0.0 if T0 is None else time.monotonic() - T0
    print(
        f"[wall {wall:6.2f}s | audio {AUDIO_SENT_S:6.2f}s] {kind} {detail}", flush=True
    )


def load_pcm(path: Path, rate: int) -> bytes:
    data, src_rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if src_rate != rate:  # crude linear resample: enough for a protocol probe
        n = int(len(mono) * rate / src_rate)
        mono = np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono)
    return (np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes()


def append(pcm: bytes) -> str:
    return json.dumps(
        {"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()}
    )


async def receiver(ws: Any, done: asyncio.Event) -> None:
    try:
        async for raw in ws:
            event = json.loads(raw)
            kind = event.get("type", "?")
            if kind.endswith(".delta"):
                log(kind, f"item={event.get('item_id')} delta={event.get('delta')!r}")
            elif kind.endswith(".completed"):
                log(
                    kind,
                    f"item={event.get('item_id')} transcript={event.get('transcript')!r}",
                )
            elif kind in ("session.created", "session.updated"):
                log(kind, json.dumps(event.get("session"))[:600])
            else:
                rest = {k: v for k, v in event.items() if k not in ("type", "event_id")}
                log(kind, json.dumps(rest)[:400])
    except websockets.ConnectionClosed as exc:
        log(
            "CLOSED",
            f"code={exc.rcvd.code if exc.rcvd else None} reason={exc.rcvd.reason if exc.rcvd else None!r}",
        )
    finally:
        done.set()


async def main() -> int:
    global T0, AUDIO_SENT_S
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--api-key-env", default="OPENAI_API_KEY")
    p.add_argument("--model", default="gpt-4o-transcribe")
    p.add_argument("--language", default="en")
    p.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    p.add_argument("--rate", type=int, default=24000)
    p.add_argument("--no-turn-detection", action="store_true")
    p.add_argument("--trailing-silence", type=float, default=2.0)
    p.add_argument("--commit-empty", action="store_true")
    p.add_argument("--idle", type=float, default=0.0)
    p.add_argument(
        "--wait", type=float, default=5.0, help="seconds to listen at the end"
    )
    args = p.parse_args()

    key = os.environ.get(args.api_key_env)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    pcm = load_pcm(args.audio, args.rate)
    silence = bytes(int(args.rate * args.trailing_silence) * 2)
    step = int(args.rate * CHUNK_S) * 2

    session: dict[str, Any] = {
        "type": "transcription",
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": args.rate},
                "transcription": {"model": args.model, "language": args.language},
                "turn_detection": None
                if args.no_turn_detection
                else {"type": "server_vad"},
            }
        },
    }
    async with websockets.connect(args.url, additional_headers=headers) as ws:
        done = asyncio.Event()
        task = asyncio.create_task(receiver(ws, done))
        await asyncio.sleep(0.5)
        await ws.send(json.dumps({"type": "session.update", "session": session}))
        await asyncio.sleep(0.5)
        if args.commit_empty:
            log("SEND", "commit on an empty buffer")
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            await asyncio.sleep(1.0)
            log("SEND", "commit on a 50 ms buffer")
            await ws.send(append(bytes(int(args.rate * 0.05) * 2)))
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            await asyncio.sleep(1.0)
            log("SEND", "clear")
            await ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
            await asyncio.sleep(1.0)
        T0 = time.monotonic()
        audio = pcm + (b"" if args.no_turn_detection else silence)
        for i in range(0, len(audio), step):
            await ws.send(append(audio[i : i + step]))
            AUDIO_SENT_S += len(audio[i : i + step]) / 2 / args.rate
            if i == len(pcm) - (len(pcm) % step or step):
                log("SPEECH END (fixture)")
            await asyncio.sleep(CHUNK_S)
        if args.no_turn_detection:
            log("SEND", "commit")
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        if args.idle:
            log("IDLE", f"sending nothing for {args.idle}s")
            try:
                await asyncio.wait_for(done.wait(), args.idle)
            except asyncio.TimeoutError:
                log("IDLE", "socket still open")
        try:
            await asyncio.wait_for(done.wait(), args.wait)
        except asyncio.TimeoutError:
            pass
        task.cancel()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
