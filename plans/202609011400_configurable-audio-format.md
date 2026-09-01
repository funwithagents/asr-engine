# Configurable audio format (rate / channels / encoding)

**Status:** Done

Implements a configurable end-to-end audio format — sample rate, channels, and
encoding — driven from `engine.audio`, with each ASR module *declaring* the
formats it supports so the engine can reconcile the configured format against
them (fail-fast by default, opt-in fallback). Replaces the four hardcoded
constants in `audio.py` and the hardcoded `encoding`/`sample_rate`/`channels`
in the Deepgram modules.

Delivers: rate + channels + encoding as first-class, per-module-validated
config. Deliberately leaves out: multi-channel *transcripts* (channels still
collapses to one transcript stream — `channels` selects the capture/wire layout,
not per-channel ASR), and file **resampling** (WAV file sources must already
match the configured rate/channels; only linear16→mulaw re-encoding is applied
to files, since that is lossless and cheap).

## Design summary

One value object carries the format through the whole pipeline:

```python
@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int = 16000
    channels: int = 1
    encoding: str = "linear16"  # "linear16" | "mulaw"
```

**Who owns what conversion.** Live capture delegates rate and channel
conversion to PortAudio, exactly as today (the current code already asks
PortAudio for 16 kHz / mono regardless of the device's native format). The only
transform the pipeline performs itself is **linear16 → mulaw**, via a small
numpy G.711 encoder — `audioop` is unavailable on Python 3.13+ (the project
targets `>=3.11`), so we must not use it.

**Reconciliation.** Each module declares the formats it supports as class
attributes (available before instantiation). The engine resolves the desired
`AudioFormat` against them per a policy, then hands the resolved format to both
the capture layer and the module.

**Declaration is required, enforced at import time.** The capability attributes
are *not* given permissive defaults on the ABC — a silent default would let a
module that forgot to declare appear to accept a format it can't handle. Instead
`ASRModule.__init_subclass__` runs when each subclass is defined and raises
`TypeError` if a concrete module (guarded by `__abstractmethods__`, so
intermediate abstract bases are exempt) omits any capability attribute, or
declares a `DEFAULT_*` that isn't a member of its `SUPPORTED_*` set (unless that
set is `None` = "any"). A module that genuinely accepts anything must still say
so explicitly (`SUPPORTED_SAMPLE_RATES = None`).

- `on_unsupported = "error"` (default): raise a clear `ValueError` at
  start-up/first-`start()` listing the module's supported values — matches the
  project's fail-fast config philosophy.
- `on_unsupported = "fallback"`: use the module's declared default for any
  unsupported dimension (for rate/channels this means opening the capture stream
  at the default — i.e. PortAudio resamples/remixes; for encoding it means
  transcoding), and log a `WARNING`.

## Scope

- `src/asr_engine/audio.py` — new `AudioFormat` dataclass; `linear16_to_mulaw()`
  numpy G.711 encoder; `AudioCapture`, `FileAudioSource`, `ScriptableAudioSource`
  parametrized by an `AudioFormat` instead of the module-level constants;
  chunk-size math derived from the format (~100 ms). Keep the constants as the
  default format's values.
- `src/asr_engine/modules/base.py` — required capability class attributes
  (`SUPPORTED_SAMPLE_RATES` / `SUPPORTED_CHANNELS` / `SUPPORTED_ENCODINGS` and
  `DEFAULT_SAMPLE_RATE` / `DEFAULT_CHANNELS` / `DEFAULT_ENCODING`) declared as
  annotations only (no defaults) and enforced by `__init_subclass__`
  (presence + `DEFAULT_* ∈ SUPPORTED_*` coherence, abstract bases exempt);
  `reconcile_audio_format(desired, module_cls, *, on_unsupported)` helper; add
  `audio_format: AudioFormat` keyword to the abstract `start()` signature.
- `src/asr_engine/modules/deepgram_v1.py` / `deepgram_v2.py` — declare supported
  formats (`{8000,16000,24000,44100,48000}`, `{1}`, `{"linear16","mulaw"}`);
  read `encoding`/`sample_rate`/`channels` from the passed `AudioFormat` instead
  of the hardcoded literals.
- `src/asr_engine/config.py` — `AudioConfig` gains `sample_rate: int = 16000`,
  `channels: int = 1`, `encoding: str = "linear16"`,
  `on_unsupported_format: str = "error"`; parse + validate (`encoding` in the
  known set, `on_unsupported_format` in `{"error","fallback"}`).
- `src/asr_engine/engine.py` — build the desired `AudioFormat` from
  `config.audio`, reconcile it against the module class, pass the resolved format
  to `AudioCapture(...)` and to `self._asr_module.start(..., audio_format=...)`.
- Specs (Implemented → Updated → Implemented): `asr-module-interface.md` (audio
  format contract becomes module-declared + reconciled; `start()` signature),
  `configuration.md` (new `engine.audio` fields), `deepgram-module.md` (declared
  formats), `architecture.md` (capture owns rate/channel conversion, pipeline
  owns encoding transcode), `engine.md` (reconciliation step).
- Tests: `tests/test_audio.py` (format-parametrized capture/file sources;
  `linear16_to_mulaw` correctness), `tests/modules/test_base.py`
  (`reconcile_audio_format`: keep-supported, error-on-unsupported,
  fallback-to-default; plus `__init_subclass__` enforcement: a module missing an
  attribute or with a `DEFAULT` outside its `SUPPORTED` set fails to define), `tests/test_config.py` (new fields + validation),
  `tests/test_engine.py` (resolved format flows to a fake module's `start()`).
- `tests-e2e/helpers.py` — no schema change required; optionally add a
  non-default-rate conformance case once the fast tier is green.

## Steps

1. `AudioFormat` dataclass + `linear16_to_mulaw()` in `audio.py`; derive
   `chunk_samples`/`chunk_bytes` from the format (~100 ms). Keep
   `SAMPLE_RATE`/`CHANNELS`/`DTYPE`/`CHUNK_SAMPLES` as the default format's
   values so existing imports keep working.
2. Parametrize `AudioCapture` (open `sd.InputStream` at the format's rate/
   channels; transcode chunks to mulaw when the format asks for it),
   `FileAudioSource`, and `ScriptableAudioSource` by an `AudioFormat`; validate
   WAV files against the format's rate/channels (re-encode to mulaw as needed).
3. Add required capability attributes + `__init_subclass__` enforcement
   (presence + `DEFAULT_* ∈ SUPPORTED_*`) + `reconcile_audio_format()` to
   `modules/base.py`; add `audio_format` kwarg to abstract `start()`.
4. Update both Deepgram modules to declare formats and use the passed
   `AudioFormat`.
5. Extend `AudioConfig` + `_parse_engine` validation in `config.py`.
6. Wire `engine.py`: build desired format, reconcile, pass to capture + module.
7. Update the five specs (flip to Updated while editing, back to Implemented
   once verified) and their `_index.md` rows; keep the Project map current.
8. Update/extend tests per Scope.

## Verification

- `uv run ruff check . && uv run ruff format .`
- `uv run pyright`
- `uv run pytest tests/` — all green, including new `AudioFormat`/reconcile/
  mulaw/config tests.
- Spot-check e2e against a non-default rate: `zsh -ic 'uv run pytest tests-e2e
  -k deepgram_v1'` with an `engine.audio.sample_rate` of 48000.

Mark `Done` (here and in [_index.md](_index.md)) and flip the five specs back to
`Implemented` only once lint + type-check + `tests/` all pass.
