---
code:
  - src/asr_engine/modules/openai_transcriptions_compatible/__init__.py
  - src/asr_engine/modules/openai_transcriptions_compatible/module.py
  - src/asr_engine/modules/__init__.py
  - pyproject.toml
tests:
  - tests/modules/test_openai_transcriptions_compatible.py
  - tests-e2e/test_engine_modules.py
---

# OpenAI Transcriptions-compatible Module

**Status:** Implemented

## Purpose

`openai_transcriptions_compatible` sends each utterance to an HTTP **`POST /v1/audio/transcriptions`** endpoint, OpenAI's batch speech-to-text API. Many servers implement it, so one module reaches all of them. It follows the project's rule that a **protocol module** gets one module named `<origin>_<api>_compatible` ([asr-module-interface.md](../asr-module-interface.md) "Optional dependencies"):

- **Hosted:** OpenAI (`gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `whisper-1`), Groq (`whisper-large-v3-turbo`), and other providers that copy the API.
- **Self-hosted:** speaches (faster-whisper), the whisper.cpp server, `mlx_audio.server`, and vLLM's batch transcription models (Whisper, Canary, …).

It is a **batch** module, a [`BatchASRModule`](../batch-asr-module.md) like `mlx_audio`: the VAD gate ends each utterance (`"required"`), and the module uploads it as one WAV file. The response's `text` is the final. Its streaming sibling is [openai-realtime-compatible.md](openai-realtime-compatible.md).

### Measured (2026-11-13, hosted OpenAI, `sample_16000_theskyisbluevalidate.wav`, 2.0 s)

| Request | Status | Time | Text |
|---|---|---|---|
| 0.5 s of silence, first request | 200 | 1.6 s | `"بہر حال"` (a hallucination on silence) |
| 0.05 s of silence | **400** | 0.6 s | "Audio file might be corrupted or unsupported" |
| speech, `gpt-4o-transcribe` | 200 | 1.0 s, then 0.5 s on a warm connection | "The sky is blue, validate." |
| speech, `gpt-4o-mini-transcribe` | 200 | 0.75 s | "The sky is blue. Validate." |
| speech, `language: "en"` | 200 | 0.66 s | "The sky is blue, validate" |
| unknown model | **404** | 0.2 s | `error.message`: "The model `nope-model` does not exist…" |
| wrong key | **401** | | `error.message`: "Incorrect API key provided…" |

- **The first request pays the connection setup** (TLS): 1.6 s against 0.5 to 1 s afterwards. A warm-up in `load()` moves that cost before `connected`.
- **Audio under 0.1 s is rejected** with a 400. The module does not send it.
- **Errors carry a JSON `error.message`**, which the module puts in its log.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Registry key / package / extra | **`openai_transcriptions_compatible`**, `modules/openai_transcriptions_compatible/`, extra **`openai-transcriptions-compatible`** = `httpx>=0.27` | The protocol-module naming rule, as for `openai_realtime_compatible`. The extra is in `all` and `dev`. |
| HTTP client | **httpx, synchronous**, one `httpx.Client` per module instance | Requests run on the base class's single worker thread, so a blocking client is the natural fit. httpx was already in the lock (through `mcp` and `mistralai`). The `openai` SDK would add a dependency and its own retries for what is one multipart POST. |
| Upload | **A 16 kHz mono 16-bit WAV** (`file`), with `response_format: "json"` | Every server accepts WAV. The conversion back from the base class's float32 is exact. JSON with a `text` field is the one response shape every implementation returns. |
| **Interims** | **Off by default** (`interim_interval_s: 0`), opt-in | Each interim re-uploads the whole growing utterance, and hosted APIs bill per request by audio length. At 0.5 s intervals a 5 s utterance costs about six times its audio. With a free local server, turning them on is reasonable. The base class gains a `DEFAULT_INTERIM_INTERVAL_S` class attribute for this. |
| **Warm-up** | **`load()` uploads 0.5 s of silence once**, and discards the text | It checks `url`, key and model before `connected`, so a wrong key fails at start, not on the first utterance. A failure goes through the base class's load retry with backoff, and is logged with the server's message and a hint. It also opens the connection, which saves the first utterance about 0.6 s (measured). The cost is a fraction of a cent, once per module instance (the client stays loaded across `stop()`/`start()`). |
| Short audio | **Under 0.1 s: not sent**, no final | Hosted OpenAI rejects it (measured), and the gate's `min_speech_duration_ms` rarely lets one through anyway. |
| Failed request | **The utterance is dropped**, logged with the HTTP status, the server's `error.message` and a hint for 401 / 403 / 404 | The base class's decode-failure rule. There is no retry: a retry would arrive after later utterances' finals, out of order. Deferred (see Open questions). |
| `api_key` | **Required for `api.openai.com`, optional elsewhere**; sent as `Authorization: Bearer` | Same rule as `openai_realtime_compatible`. Local servers usually take no key. |
| Other request fields | **`extra_fields`**: an object sent as extra form fields | Servers add their own (`temperature`, Groq's options, …). The module adds no per-server knowledge. The fields it sets itself (`file`, `model`, `language`, `prompt`, `response_format`, `stream`) cannot be overridden. |
| Sample rate | **16 kHz** (the base class's) | What batch speech models take, and what Silero analyses natively. Servers resample anything else anyway. |

## Configuration fields

| Field | Type | Default | Description |
|---|---|---|---|
| `url` | string | `"https://api.openai.com/v1/audio/transcriptions"` | The full endpoint URL, used verbatim (`http://` or `https://`) |
| `api_key` / `api_key_env` | string | — | Literal key, or the name of the environment variable holding it. Required for hosted OpenAI |
| `model` | string | `"gpt-4o-transcribe"` | The `model` form field. Required by the API; some local servers ignore it |
| `language` | string or null | `null` | The `language` form field (ISO 639-1 on OpenAI, e.g. `"en"`); unset lets the server detect it |
| `prompt` | string or null | `null` | The `prompt` form field: vocabulary or context hints |
| `extra_fields` | object | `{}` | More form fields, values string, number or boolean (booleans sent as `true` / `false`) |
| `request_timeout_s` | float | `30.0` | Timeout of each HTTP request |
| `interim_interval_s` | float | **`0`** | From [batch-asr-module.md](../batch-asr-module.md), with interims off by default here |
| `max_utterance_s`, `stop_timeout_s` | | | From [batch-asr-module.md](../batch-asr-module.md) |

Validation in `__init__`: `url` is an `http(s)://` string. A key is resolved when `api_key` or `api_key_env` is given, and one is required when the host is `api.openai.com`. `model` is a non-empty string. `language` and `prompt` are null or non-empty strings. `extra_fields` is an object of string keys to scalar values, and names none of the module's own fields. `request_timeout_s` is a positive number. Plus the base-class fields. Each failure raises `ValueError` naming the field.

### Examples

```json
{ "type": "openai_transcriptions_compatible", "api_key_env": "OPENAI_API_KEY", "language": "en" }
```

```json
{
  "type": "openai_transcriptions_compatible",
  "url": "https://api.groq.com/openai/v1/audio/transcriptions",
  "api_key_env": "GROQ_API_KEY",
  "model": "whisper-large-v3-turbo"
}
```

```json
{
  "type": "openai_transcriptions_compatible",
  "url": "http://localhost:8000/v1/audio/transcriptions",
  "model": "Systran/faster-whisper-small",
  "interim_interval_s": 0.5
}
```

`engine.vad` is mandatory (`{"type": "silero"}`), and `engine.audio.sample_rate` is 16000 (the default).

## Runtime

The base class owns buffering, scheduling, ordering and the lifecycle ([batch-asr-module.md](../batch-asr-module.md) "Runtime"). The module adds:

- **`load()`**: create the `httpx.Client` (headers, timeout) on first call, then post the warm-up. Runs on the worker thread.
- **`transcribe(audio)`**: return `""` under 0.1 s. Otherwise encode the WAV, post it with the form fields, and return the response's `text`. A transport error, a status ≥ 400, a non-JSON body or a body without a `text` string raises `TranscriptionError`, which the base class logs. The request then yields no final.

Tests inject an `httpx` transport (`transport=` keyword, an `httpx.MockTransport`) in place of the network.

## Testing

### Fast tier: `tests/modules/test_openai_transcriptions_compatible.py`

An in-process server (`httpx.MockTransport`) parses each multipart upload: headers, form fields, and the WAV decoded back to samples.

- **Protocol:** an utterance is uploaded as a 16 kHz mono WAV with exactly the queued samples, the configured form fields (with `extra_fields`, and `response_format: "json"`), and the bearer key. The response's `text`, stripped, is the final. No key means no `Authorization` header.
- **Warm-up:** exactly one, 0.5 s of silence, before `connected`, and none on a later `start()`. A failed warm-up (404, or an unreachable server) is retried, and its log carries the server's message and hint.
- **Interims:** none by default; with `interim_interval_s: 0.5`, one per half second of audio, then the final.
- **Short audio:** an utterance under 0.1 s is not sent.
- **Failed request:** a 401 (logged with its hint), a 500, a non-JSON body and a body without `text` each drop that utterance, and the next one transcribes.
- **Config:** hosted OpenAI requires a key; `api_key_env` is read; one case per validation rule; the registry resolves the key.

### Live tier: `tests-e2e/test_engine_modules.py`

A `MODULES` row: type `openai_transcriptions_compatible`, `gpt-4o-transcribe` on hosted OpenAI, the 16 kHz WAV `ModuleAudio`, a `silence_s` of 3.0, and `OPENAI_API_KEY`. Being `"required"`, it is listed in `helpers.VAD_REQUIRED_MODULES` and runs only in `test_engine_modules_vad`. CI runs it on the live job with the `OPENAI_API_KEY` secret. Verify with `zsh -ic 'uv run pytest tests-e2e -k openai_transcriptions_compatible'`.

## Files

- `src/asr_engine/modules/openai_transcriptions_compatible/__init__.py`: imports nothing
- `src/asr_engine/modules/openai_transcriptions_compatible/module.py`: `OpenAITranscriptionsCompatibleModule(BatchASRModule)`
- `src/asr_engine/modules/batch.py`: `DEFAULT_INTERIM_INTERVAL_S`
- `src/asr_engine/modules/__init__.py`: the registry entry, `extra="openai-transcriptions-compatible"`
- `pyproject.toml` / `uv.lock`: the extra, in `all` and `dev`
- `examples/config/openai_transcriptions_compatible.json`, `tests/examples/test_configs.py`
- `tests/modules/test_openai_transcriptions_compatible.py`, `tests-e2e/helpers.py`
- Doc touch-ups: [asr-module-interface.md](../asr-module-interface.md) (registry, extras table), [batch-asr-module.md](../batch-asr-module.md) (the interim default), [vad.md](../vad.md) (module tables), [e2e-testing.md](../e2e-testing.md) (rows), [configuration.md](../configuration.md), [ci.md](../ci.md) (expected skips), `README.md`, `examples/README.md`, `AGENTS.md`

## Open questions

None load-bearing. Deferrals:

- **Retrying a failed request** (429, 5xx, timeout). It would have to keep finals in order, so a retry delays every later utterance. It is worth adding if transient failures show up in practice.
- **Streaming responses** (`stream: true`, server-sent `transcript.text.delta` events on OpenAI's `gpt-4o-*` models). They would give interims from the final's own request, at no extra cost. Few compatible servers implement them.
- **Compressed uploads** (FLAC, Opus) to cut upload time on slow links. A 10 s WAV is 320 kB.
