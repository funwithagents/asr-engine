"""Measure the Kyutai STT real-time factor (RTF) on this machine — MLX backend.

The `kyutai` module's whole backpressure design assumes the model keeps up with
real time (RTF < 1: one 80 ms frame processed in under 80 ms). Run this on new
hardware before trusting the module there (see specs/modules/kyutai.md).

It loads the model exactly as the module's MLX backend does, feeds an audio file
frame by frame, and reports wall-clock per step, the overall RTF, the transcript
and the end-of-turn (semantic VAD) probabilities.

Usage:
    uv run python scripts/benchmark_kyutai.py
    uv run python scripts/benchmark_kyutai.py --audio some.wav --trailing-silence 3
    uv run python scripts/benchmark_kyutai.py --hf-repo kyutai/stt-2.6b-en-mlx

Requires the `kyutai-mlx` extra (Apple Silicon): `uv sync --extra kyutai-mlx`.
The first run downloads the weights (~2 GB for the 1B model) from Hugging Face.
"""

# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

from __future__ import annotations

import argparse
import json
import statistics
import time
from importlib.metadata import version
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIO = REPO_ROOT / "tests-e2e/fixtures/sample_44100_theskyisblue.mp3"

SAMPLE_RATE = 24000
FRAME_SAMPLES = 1920  # 80 ms
FRAME_S = FRAME_SAMPLES / SAMPLE_RATE


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--hf-repo", default="kyutai/stt-1b-en_fr-candle")
    parser.add_argument("--max-steps", type=int, default=4096)
    parser.add_argument(
        "--trailing-silence",
        type=float,
        default=3.0,
        help="seconds of silence appended so the delayed text and VAD head land",
    )
    args = parser.parse_args()

    import mlx.core as mx
    import mlx.nn as nn
    import rustymimi
    import sentencepiece
    import sphn
    from huggingface_hub import hf_hub_download
    from moshi_mlx import models, utils

    print(f"moshi_mlx {version('moshi_mlx')}, mlx {version('mlx')}")

    t0 = time.perf_counter()
    with open(hf_hub_download(args.hf_repo, "config.json")) as f:
        raw_config = json.load(f)
    mimi_weights = hf_hub_download(args.hf_repo, raw_config["mimi_name"])
    moshi_weights = hf_hub_download(
        args.hf_repo, raw_config.get("moshi_name", "model.safetensors")
    )
    tokenizer_path = hf_hub_download(args.hf_repo, raw_config["tokenizer_name"])
    t_download = time.perf_counter() - t0

    t0 = time.perf_counter()
    lm_config = models.LmConfig.from_config_dict(raw_config)
    model = models.Lm(lm_config)
    model.set_dtype(mx.bfloat16)
    if moshi_weights.endswith(".q4.safetensors"):
        nn.quantize(model, bits=4, group_size=32)
    elif moshi_weights.endswith(".q8.safetensors"):
        nn.quantize(model, bits=8, group_size=64)
    if args.hf_repo.endswith("-candle"):
        model.load_pytorch_weights(moshi_weights, lm_config, strict=True)
    else:
        model.load_weights(moshi_weights, strict=True)
    text_tokenizer = sentencepiece.SentencePieceProcessor(tokenizer_path)
    other_codebooks = lm_config.other_codebooks
    mimi_codebooks = max(lm_config.generated_codebooks, other_codebooks)
    audio_tokenizer = rustymimi.Tokenizer(mimi_weights, num_codebooks=mimi_codebooks)
    model.warmup()
    gen = models.LmGen(
        model=model,
        max_steps=args.max_steps,
        text_sampler=utils.Sampler(top_k=25, temp=0),
        audio_sampler=utils.Sampler(top_k=250, temp=0.8),
        check=False,
    )
    t_load = time.perf_counter() - t0

    has_vad = lm_config.extra_heads_num_heads > 0
    stt_config = raw_config.get("stt_config", {})
    print(f"repo            : {args.hf_repo}")
    print(f"stt_config      : {stt_config}")
    print(f"extra heads     : {lm_config.extra_heads_num_heads} (VAD: {has_vad})")
    print(f"download/cache  : {t_download:.1f} s")
    print(f"load + warm-up  : {t_load:.1f} s")

    pcm, _ = sphn.read(str(args.audio), sample_rate=SAMPLE_RATE)
    audio = pcm[0].astype(np.float32)
    prefix = np.zeros(
        int(stt_config.get("audio_silence_prefix_seconds", 0.0) * SAMPLE_RATE),
        dtype=np.float32,
    )
    silence = np.zeros(int(args.trailing_silence * SAMPLE_RATE), dtype=np.float32)
    audio = np.concatenate([prefix, audio, silence])
    n_frames = len(audio) // FRAME_SAMPLES

    step_times: list[float] = []
    pieces: list[str] = []
    piece_times: list[tuple[float, str]] = []
    vad_trace: list[tuple[float, float]] = []
    n_heads = 0
    for i in range(n_frames):
        frame = audio[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES]
        t0 = time.perf_counter()
        tokens = audio_tokenizer.encode_step(frame[None, None, :])
        tokens = mx.array(tokens).transpose(0, 2, 1)[:, :, :other_codebooks]
        if has_vad:
            text_token, vad_heads = gen.step_with_extra_heads(tokens[0])
            n_heads = len(vad_heads)
            p_end = vad_heads[2][0, 0, 0].item()
        else:
            text_token = gen.step(tokens[0])
            p_end = None
        token_id = text_token[0].item()
        step_times.append(time.perf_counter() - t0)
        if token_id not in (0, 3):
            pieces.append(text_tokenizer.id_to_piece(token_id).replace("▁", " "))
            piece_times.append(((i + 1) * FRAME_S, pieces[-1]))
        if p_end is not None:
            vad_trace.append(((i + 1) * FRAME_S, p_end))

    total = sum(step_times)
    audio_s = n_frames * FRAME_S
    ordered = sorted(step_times)
    print(f"audio           : {audio_s:.2f} s in {n_frames} frames ({args.audio.name})")
    print(f"transcript      : {''.join(pieces).strip()!r}")
    print("text timeline   : " + " ".join(f"{t:.2f}s{p!r}" for t, p in piece_times))
    print(
        "step wall-clock : "
        f"mean {statistics.mean(step_times) * 1e3:.1f} ms, "
        f"median {statistics.median(step_times) * 1e3:.1f} ms, "
        f"p95 {ordered[int(len(ordered) * 0.95)] * 1e3:.1f} ms, "
        f"max {ordered[-1] * 1e3:.1f} ms  (budget: {FRAME_S * 1e3:.0f} ms)"
    )
    print(f"RTF             : {total / audio_s:.3f}  (< 1 keeps up with real time)")
    if vad_trace:
        print(f"VAD heads       : {n_heads}; end-of-turn p = vad_heads[2][0, 0, 0]")
        above = [t for t, p in vad_trace if p > 0.5]
        if above:
            print(f"  first p > 0.5 at {above[0]:.2f} s; {len(above)} frames above")
        else:
            print("  p never exceeded 0.5")
        print("  trace: " + " ".join(f"{p:.2f}" for _, p in vad_trace))


if __name__ == "__main__":
    main()
