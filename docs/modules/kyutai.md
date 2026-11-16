# `kyutai`

**Kind:** local · **Backend:** Kyutai streaming STT, on-device · **Extra:** `kyutai-mlx` or `kyutai-torch` · **Credentials:** none · **VAD gate:** optional

[Kyutai's streaming STT](https://github.com/kyutai-labs/delayed-streams-modeling) runs inside the process: no network, no API key, no per-minute cost. It transcribes English and French, streams text as the audio arrives, and detects the end of a turn by itself.

One module type covers two interchangeable backends: MLX on Apple Silicon (`kyutai-mlx`) and PyTorch elsewhere (`kyutai-torch`). `"backend": "auto"` picks MLX when it is available, so the same config works on both.

> The PyTorch backend has not yet been verified on real hardware. A report of how it behaves on your machine is welcome at the issue tracker.

```bash
uv sync --no-default-groups --extra kyutai-mlx     # Apple Silicon
uv sync --no-default-groups --extra kyutai-torch   # elsewhere
# For an installed package:
pip install 'asr-engine[kyutai-mlx]'               # or 'asr-engine[kyutai-torch]'
```

The Kyutai extras cannot be installed together with `mlx-audio`, and so are not part of `all`: install them in an environment of their own ([why](index.md#installing-a-module)).

```json
{
  "module": {
    "type": "kyutai",
    "backend": "auto",
    "hf_repo": "kyutai/stt-1b-en_fr-candle"
  }
}
```

The model takes 24 kHz only. There is nothing to set: the microphone is opened at 24 kHz, and a file or source at another rate is resampled ([audio-input.md](../audio-input.md#audio-format-and-conversion)).

| Field | Default | Description |
|---|---|---|
| `backend` | `auto` | `auto`, `mlx` or `torch` |
| `hf_repo` | `kyutai/stt-1b-en_fr-candle` | Hugging Face repo to load. Only the `-candle` 1B repo carries the end-of-turn heads |
| `vad` | `true` | Close utterances on the model's own end-of-turn detection |
| `vad_threshold` | `0.5` | End-of-turn probability above which an utterance closes |
| `finalize_after_silence_s` | `2.0` | Fallback: close an utterance after this long with no new text |
| `max_steps` | `4096` | Model step budget (about 5.5 min) before the session is recycled at an utterance boundary |
| `device` | `auto` | PyTorch backend only: `auto`, `cuda`, `mps` or `cpu` |
| `lag_warn_s` / `lag_drop_s` | `2.0` / `10.0` | If the model falls behind real time: warn, then drop the oldest audio |

## Two different `vad` settings

`module.vad` switches the model's own end-of-turn detection. `engine.vad` is the optional [speech gate](../vad.md) in front of any module. For Kyutai the gate is usually left out: the model is local and free and ends turns by itself, so gating only saves compute.

## Weights and warm-up

The first `start()` downloads the weights (about 2 GB for the 1B model) and loads them. To fetch them ahead of time:

```bash
python -m asr_engine.modules.kyutai.download
```

`engine.status()["connected"]` turns true once the model is warm. The model then stays in memory across `stop()` / `start()` and `listen()` calls.

To check that a machine keeps up with real time, run `uv run python scripts/benchmark_kyutai.py` from a checkout. On an Apple M5 Pro the real-time factor is about 0.45.

## Troubleshooting

- An `ImportError` naming `kyutai-mlx` or `kyutai-torch`: the backend's extra is not installed in this environment.
- Engine construction fails on `engine.module.sample_rate`: the model takes 24000 only. Remove the field.
- A warning that the model is lagging, then dropped audio: the machine does not keep up with real time. Run the benchmark above.
- Utterances only close after two seconds of silence: the selected `hf_repo` has no end-of-turn heads, so only the `finalize_after_silence_s` fallback applies. Use the `-candle` repo.

## See also

- [specs/modules/kyutai.md](../../specs/modules/kyutai.md): the module's contract, the models and the measurements.
- [examples/config/kyutai.json](../../examples/config/kyutai.json): a complete server config.
- [`mlx-audio`](mlx-audio.md): the other local module, for multilingual batch models on Apple Silicon.
