---
code:
  - src/asr_engine/modules/mlx_audio/__init__.py
  - src/asr_engine/modules/mlx_audio/module.py
  - src/asr_engine/modules/mlx_audio/runner.py
  - src/asr_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_mlx_audio.py
  - tests-e2e/test_engine_modules.py
---

# mlx-audio Module

**Status:** Implemented

## Purpose

`mlx_audio` runs local speech-to-text models on **Apple Silicon** through [mlx-audio](https://github.com/Blaizzy/mlx-audio), a generic MLX runner for about thirty STT model families: Parakeet, Whisper, Qwen3-ASR, Canary, SenseVoice, Moonshine, Voxtral and more. It follows the project's rule that a **generic model runner gets one module**, and the model is a config value: `"model": "mlx-community/parakeet-tdt-0.6b-v3"` and `"model": "mlx-community/Qwen3-ASR-0.6B-8bit"` are the same module.

It is a **batch** module, the first [`BatchASRModule`](../batch-asr-module.md): it needs the VAD gate (`"required"`), decodes each utterance on the gate's end-of-speech marker, and shows interims by re-decoding the growing utterance. On-device, free and private, with no API key.

### Measured (2026-11-13, Apple Silicon, mlx-audio 0.6.5, on `sample_16000_theskyisbluevalidate.wav`)

| Model | Load (cached) | Warm-up (1 s of zeros) | Decode 1 s / 2 s after warm-up | Text |
|---|---|---|---|---|
| `mlx-community/parakeet-tdt-0.6b-v3` | 0.9 s (38 s on first download) | 0.5 s | 0.02 s / 0.03 s | "The sky is blue, validate." |
| `mlx-community/Qwen3-ASR-0.6B-8bit` (`language: "English"`) | 16 s | 1.4 s | 0.06 s / 0.10 s | "The sky is blue. Validate." |
| `mlx-community/whisper-large-v3-turbo-asr-4bit` (`language: "en"`) | 8 s | 1.6 s | 0.21 s / 0.22 s | " The sky is blue, validate." |

- **Warm-up is load-bearing.** Without it the first real decode took 2.7 s (graph compilation). After it, 0.02 s.
- **Not every repo loads.** `mlx-community/whisper-tiny` lacks the Hugging Face processor (tokenizer), and `generate()` raises "Processor not found". The `-asr-` Whisper repos include it.
- **`generate()` keyword arguments differ by family.** Whisper takes `language="en"`, Qwen3-ASR takes `language="English"`, and Parakeet takes none. Every family accepts an `mx.array` of float32 audio and returns an object with `.text`.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Registry key / package | **`mlx_audio`**, `modules/mlx_audio/` | A module per runner, named after it ([asr-module-interface.md](../asr-module-interface.md) "Optional dependencies"). |
| Extra | **`mlx-audio`** = `mlx-audio[stt]>=0.6`, marker-guarded to Apple Silicon | The `stt` extra of mlx-audio adds what its STT models need. The marker keeps `uv lock` working on other platforms, as for `kyutai-mlx`. |
| **Conflict with Kyutai** | **`mlx-audio` and `kyutai-mlx` / `kyutai-torch` are mutually exclusive extras** | `moshi-mlx` (behind `kyutai-mlx`) pins `sounddevice==0.5` and `mlx<0.27`, and `moshi` (behind `kyutai-torch`) also pins `sounddevice==0.5`. Every mlx-audio ≥ 0.4 needs `sounddevice>=0.5.3` and `mlx>=0.31`. The last mlx-audio that coexists, 0.2.8, has four model families and no Qwen3-ASR. So `mlx-audio` is declared in `[tool.uv] conflicts`, stays out of `all` and `dev`, and is installed in an environment of its own. See [Installation](#installation). |
| Model config | **`model`** (Hugging Face repo or local path) and **`generate_kwargs`** (passed through to `generate()`) | The runner is generic, so the module adds no per-family knowledge. A wrong keyword fails at warm-up, during load, with the runner's own error. |
| Default model | **`mlx-community/parakeet-tdt-0.6b-v3`** | The default in speech-to-speech. The fastest of the three measured, 25 European languages with automatic detection, and no keyword arguments needed. |
| Input | **`mx.array` of float32** | The one input type every family accepts (Parakeet rejects NumPy). |
| Import | **`runner.py` imports mlx-audio; `module.py` imports nothing third-party** | As with `kyutai`: the registry entry has `extra=None` and the module raises its own install hint, so the config validation and the base class are testable on every platform. |

## Configuration fields

| Field | Type | Default | Description |
|---|---|---|---|
| `model` | string | `"mlx-community/parakeet-tdt-0.6b-v3"` | Hugging Face repo id or local path, passed to `mlx_audio.stt.utils.load_model` |
| `generate_kwargs` | object | `{}` | Keyword arguments for `model.generate()`, e.g. `{"language": "en"}` for Whisper or `{"language": "English"}` for Qwen3-ASR |
| `interim_interval_s`, `max_utterance_s`, `stop_timeout_s` | | | From [batch-asr-module.md](../batch-asr-module.md) |

Validation in `__init__`: `model` is a non-empty string; `generate_kwargs` is an object with string keys; the base-class fields. Then, unless a test injects a loader: the platform is macOS on arm64, and `mlx_audio` is importable. Otherwise it raises `ImportError` with the install hint: *"ASR module 'mlx_audio' requires the optional dependency 'mlx-audio' (Apple Silicon only), which is not installed. It cannot share an environment with kyutai-mlx / kyutai-torch: install it with pip install 'asr-engine[mlx-audio]' in its own environment (see specs/modules/mlx-audio.md)."*

### Examples

```json
{ "type": "mlx_audio" }
```

```json
{
  "type": "mlx_audio",
  "model": "mlx-community/Qwen3-ASR-0.6B-8bit",
  "generate_kwargs": { "language": "English" }
}
```

`engine.vad` is mandatory (`{"type": "silero"}`), and `engine.audio.sample_rate` is 16000 (the default).

## Runner

`runner.py` holds `MlxAudioRunner`, the only code that touches mlx-audio. It exposes `load(model, generate_kwargs)`, which loads and warms on 1 s of zeros, and `transcribe(audio) → str`, which returns `generate(mx.array(audio), **generate_kwargs).text.strip()`. The module's `load()` / `transcribe()` delegate to it on the base class's worker thread. Tests inject a `load_model` callable in its place, `(model, generate_kwargs) → object with transcribe(np.ndarray) → str`.

## Installation

```toml
[project.optional-dependencies]
mlx-audio = ["mlx-audio[stt]>=0.6; sys_platform == 'darwin' and platform_machine == 'arm64'"]

[tool.uv]
conflicts = [
    [{ extra = "kyutai-mlx" }, { extra = "mlx-audio" }],
    [{ extra = "kyutai-torch" }, { extra = "mlx-audio" }],
    [{ extra = "all" }, { extra = "mlx-audio" }],
    [{ group = "dev" }, { extra = "mlx-audio" }],
]
```

- **`all`** keeps both Kyutai backends and leaves `mlx-audio` out. It cannot hold both, and Kyutai came first. `all` therefore means "every extra that can be installed together". [project.md](../project.md) records the exception.
- **`dev`** keeps Kyutai. It names `moshi-mlx` directly rather than `asr-engine[kyutai-mlx]`: with a conflict declared on that extra, uv 0.11 silently installs nothing for a self-referenced extra inside a group (observed while locking this change).
- **Users:** `pip install 'asr-engine[mlx-audio]'` (plus `silero` for the gate), in an environment without the Kyutai extras.
- **Contributors** get a second environment for the live tier:

  ```bash
  UV_PROJECT_ENVIRONMENT=.venv-mlx-audio uv run --no-default-groups \
      --extra mlx-audio --extra silero \
      --with pytest --with pytest-asyncio --with pytest-timeout --with pytest-mock \
      pytest tests-e2e -k mlx_audio
  ```

  `.venv-mlx-audio/` is git-ignored. The fast tier never needs mlx-audio: it injects a fake loader.

## Testing

### Fast tier: `tests/modules/test_mlx_audio.py`

Through the injected `load_model`: the configured model and `generate_kwargs` reach the loader, and the loader's text comes out as the final. The install hint is raised off Apple Silicon or without the package, monkeypatched in both directions. Validation, one case per rule. The registry resolves the key without importing mlx-audio. The batch behaviour itself is pinned once, in `test_batch.py`.

### Live tier: `tests-e2e/test_engine_modules.py`

A `MODULES` row: type `mlx_audio`, the default model, the 16 kHz WAV `ModuleAudio`, and a `silence_s` of 2.0. The row is opt-in like `kyutai`: `LOCAL_MODEL_OPT_IN_ENV` gains `"mlx_audio": "ASR_ENGINE_E2E_MLX_AUDIO"`, which vouches that the weights are downloaded, and `require_local_model` skips when the module can't be built (no package). Being `"required"`, it runs only in `test_engine_modules_vad`. Verify with the command under [Installation](#installation) plus `ASR_ENGINE_E2E_MLX_AUDIO=1`. CI never runs it: the live job is Linux, and mlx-audio is Apple Silicon only.

## Files

- `src/asr_engine/modules/mlx_audio/__init__.py`: imports nothing
- `src/asr_engine/modules/mlx_audio/module.py`: `MlxAudioModule(BatchASRModule)`
- `src/asr_engine/modules/mlx_audio/runner.py`: `MlxAudioRunner`
- `src/asr_engine/modules/__init__.py`: `"mlx_audio": LazyModule("asr_engine.modules.mlx_audio.module:MlxAudioModule")`
- `pyproject.toml` / `uv.lock`, `.gitignore`
- `examples/config/mlx_audio.json`, `tests/examples/test_configs.py`
- `tests/modules/test_mlx_audio.py`, `tests-e2e/helpers.py`, `tests-e2e/test_engine_modules.py`
- Doc touch-ups: [asr-module-interface.md](../asr-module-interface.md) (registry, extras table), [project.md](../project.md) (extras rule, layout), [vad.md](../vad.md) (module tables), [e2e-testing.md](../e2e-testing.md) (rows, `"required"` rule), [configuration.md](../configuration.md), [ci.md](../ci.md) (expected skips), `README.md`, `AGENTS.md`

## Open questions

None load-bearing. Deferrals:

- **Streaming models.** mlx-audio's Parakeet and `voxtral_realtime` have streaming modes (`stream=True`) that could make this module `"supported"` for them. A later, separate path.
- **Language memory** across utterances (Qwen3-ASR, Whisper on short windows): see [batch-asr-module.md](../batch-asr-module.md).
- **Kyutai on mlx-audio.** If mlx-audio gains a Kyutai STT model, `kyutai-mlx` could move onto it and the conflict would disappear.
