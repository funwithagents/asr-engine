"""Probe Mistral's Voxtral 3 realtime transcription API before building the module.

specs/modules/mistral.md (Draft) leaves a few load-bearing facts the public docs
don't state. This script measures them against the live API:

- **Sample-rate acceptance** — does a session at 24000 / 44100 Hz work, or only
  16000? (Run once per fixture rate.)
- **Text-after-`turn.end` lag** — how long after `turn.end` the turn's last
  `transcription.text.delta` arrives. This sizes the module's final-deferral margin.
- **Idle behaviour** — with ``--idle`` N, the script sends nothing for N seconds
  after the audio and reports whether the server closes the socket (idle timeout).
  ``--idle-silence`` streams digital silence instead, to check that it keeps the
  session alive.

It speaks the raw WebSocket protocol (session.update / input_audio.append /
input_audio.end), exactly as the module will, and logs every server event with
the wall-clock time since the first audio chunk and the audio time sent so far.

Usage (the key lives in ~/.zshrc, so go through an interactive zsh):
    zsh -ic 'uv run python scripts/probe_voxtral_realtime.py'
    zsh -ic 'uv run python scripts/probe_voxtral_realtime.py \
        --audio tests-e2e/fixtures/sample_24000_theskyisbluevalidate.wav'
    zsh -ic 'uv run python scripts/probe_voxtral_realtime.py --idle 60'
    zsh -ic 'uv run python scripts/probe_voxtral_realtime.py --no-turn-detection'

Reads the API key from the env var named by ``--api-key-env`` (default
MISTRAL_API_KEY) and never prints it.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import soundfile as sf
import websockets

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIO = REPO_ROOT / "tests-e2e/fixtures/sample_16000_theskyisbluevalidate.wav"
DEFAULT_URL = "wss://api.mistral.ai/v1/audio/transcriptions/realtime"
DEFAULT_MODEL = "voxtral-mini-transcribe-realtime-3"
CHUNK_S = 0.1


@dataclass
class Clock:
    """Wall time since the first chunk, and audio seconds sent so far."""

    t0: float | None = None
    audio_sent_s: float = 0.0

    def wall(self) -> float:
        return 0.0 if self.t0 is None else time.monotonic() - self.t0


@dataclass
class Observations:
    deltas: list[tuple[float, str]] = field(default_factory=list)  # (wall, text)
    turn_starts: list[float] = field(default_factory=list)  # wall times
    turn_ends: list[float] = field(default_factory=list)  # wall times
    done_text: str | None = None
    closed: str | None = None


def log(clock: Clock, kind: str, detail: str = "") -> None:
    print(
        f"[wall {clock.wall():6.2f}s | audio {clock.audio_sent_s:6.2f}s] {kind} {detail}",
        flush=True,
    )


def load_pcm(path: Path) -> tuple[bytes, int]:
    data, rate = sf.read(path, dtype="int16", always_2d=True)
    if data.shape[1] != 1:
        sys.exit(f"{path}: {data.shape[1]} channels; the realtime API takes mono only")
    return data[:, 0].astype("<i2").tobytes(), int(rate)


def session_update(args: argparse.Namespace, sample_rate: int) -> dict[str, Any]:
    session: dict[str, Any] = {
        "audio_format": {"encoding": "pcm_s16le", "sample_rate": sample_rate},
        "target_streaming_delay_ms": args.delay_ms,
    }
    if args.language:
        session["language"] = args.language
    if not args.no_turn_detection:
        session["turn_detection"] = {}
    return {"type": "session.update", "session": session}


async def receive(ws: Any, clock: Clock, obs: Observations) -> None:
    try:
        async for raw in ws:
            event = json.loads(raw)
            kind = event.get("type", "?")
            if kind == "transcription.text.delta":
                obs.deltas.append((clock.wall(), event["text"]))
                log(clock, kind, repr(event["text"]))
            elif kind.startswith("turn."):
                if kind == "turn.start":
                    obs.turn_starts.append(clock.wall())
                elif kind == "turn.end":
                    obs.turn_ends.append(clock.wall())
                extra = {k: v for k, v in event.items() if k != "type"}
                log(clock, kind, json.dumps(extra))
            elif kind == "transcription.done":
                obs.done_text = event.get("text")
                log(clock, kind, repr(obs.done_text))
                return
            elif kind in ("session.created", "session.updated"):
                log(clock, kind, json.dumps(event.get("session"), indent=None))
            else:
                log(clock, kind, json.dumps(event))
    except websockets.ConnectionClosed as exc:
        obs.closed = f"code={exc.rcvd.code if exc.rcvd else None} reason={exc.rcvd.reason if exc.rcvd else ''!r}"
        log(clock, "CONNECTION CLOSED", obs.closed)


async def send_paced(ws: Any, clock: Clock, pcm: bytes, sample_rate: int) -> None:
    """Send ``pcm`` in 100 ms chunks at real-time pace."""
    chunk_bytes = int(sample_rate * CHUNK_S) * 2
    for i in range(0, len(pcm), chunk_bytes):
        chunk = pcm[i : i + chunk_bytes]
        if clock.t0 is None:
            clock.t0 = time.monotonic()
        await ws.send(
            json.dumps(
                {
                    "type": "input_audio.append",
                    "audio": base64.b64encode(chunk).decode(),
                }
            )
        )
        clock.audio_sent_s += len(chunk) / 2 / sample_rate
        # Pace against the audio clock so drift doesn't accumulate.
        await asyncio.sleep(max(0.0, clock.t0 + clock.audio_sent_s - time.monotonic()))


def report(obs: Observations) -> None:
    print("\n=== summary ===")
    streamed = "".join(text for _, text in obs.deltas)
    print(f"deltas joined:      {streamed!r}")
    print(f"transcription.done: {obs.done_text!r}")
    if obs.closed:
        print(f"server closed:      {obs.closed}")
    # Text between a turn.end and the next turn.start (or turn.end) is either the
    # closing turn's tail or speech the server opened no turn for; both matter to the
    # module's final-deferral margin, so report how soon after turn.end it lands.
    for n, end in enumerate(obs.turn_ends):
        bounds = [t for t in obs.turn_starts + obs.turn_ends if t > end]
        nxt = min(bounds, default=float("inf"))
        late = [(t, txt) for t, txt in obs.deltas if end < t < nxt]
        if late:
            print(
                f"turn.end #{n + 1} at {end:.2f}s: {len(late)} delta(s) before the next "
                f"turn event, first {(late[0][0] - end) * 1000:.0f} ms / last "
                f"{(late[-1][0] - end) * 1000:.0f} ms later: "
                f"{''.join(t for _, t in late)!r}"
            )
        else:
            print(
                f"turn.end #{n + 1} at {end:.2f}s: no text before the next turn event"
            )


async def run(args: argparse.Namespace) -> None:
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        sys.exit(f"${args.api_key_env} is not set (source ~/.zshrc: run via `zsh -ic`)")

    pcm, rate = load_pcm(args.audio)
    print(f"audio: {args.audio.name} — {rate} Hz, {len(pcm) / 2 / rate:.2f}s")
    silence = bytes(int(rate * args.trailing_silence) * 2)

    clock, obs = Clock(), Observations()
    url = f"{args.url}?model={args.model}"
    async with websockets.connect(
        url, additional_headers={"Authorization": f"Bearer {api_key}"}
    ) as ws:
        receiver = asyncio.create_task(receive(ws, clock, obs))
        await ws.send(json.dumps(session_update(args, rate)))
        try:
            await send_paced(ws, clock, pcm + silence, rate)
            if args.idle:
                if args.idle_silence:
                    log(clock, "IDLE", f"streaming {args.idle:.0f}s of silence")
                    await send_paced(ws, clock, bytes(int(rate * args.idle) * 2), rate)
                else:
                    log(clock, "IDLE", f"sending nothing for {args.idle:.0f}s")
                    await asyncio.wait({receiver}, timeout=args.idle)
            if not receiver.done():
                log(clock, "SEND", "input_audio.end")
                await ws.send(json.dumps({"type": "input_audio.end"}))
                await asyncio.wait_for(receiver, timeout=args.done_timeout)
        except websockets.ConnectionClosed:
            pass  # the receiver logged it
        except TimeoutError:
            log(clock, "TIMEOUT", f"no transcription.done within {args.done_timeout}s")
        finally:
            receiver.cancel()
    report(obs)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--api-key-env", default="MISTRAL_API_KEY")
    parser.add_argument("--delay-ms", type=int, default=480)
    parser.add_argument("--language", default=None, help="ISO 639-1, e.g. en")
    parser.add_argument("--no-turn-detection", action="store_true")
    parser.add_argument("--trailing-silence", type=float, default=4.0)
    parser.add_argument(
        "--idle", type=float, default=0.0, help="seconds to idle after the audio"
    )
    parser.add_argument(
        "--idle-silence",
        action="store_true",
        help="during --idle, stream digital silence instead of sending nothing",
    )
    parser.add_argument("--done-timeout", type=float, default=15.0)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
