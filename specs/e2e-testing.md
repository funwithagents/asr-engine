---
code:
  - src/asr_engine/audio.py
tests:
  - tests-e2e/test_engine_modules.py
  - tests-e2e/test_mcp_resource.py
  - tests-e2e/test_mcp_tools.py
  - tests-e2e/test_asr_to_terminal.py
---

# E2E File-Based Testing

**Status:** Implemented

## Goal

Provide an automated end-to-end test that exercises the full pipeline — from audio
source through ASR backend to MCP client — without human interaction or a live
microphone. Audio is read from a pre-recorded WAV fixture and fed into the real
Deepgram API.

## Scope

The tests cover the complete data path at two levels, and split along a second axis —
**what varies per ASR module vs what does not**:

- **Per module** (parametrized): only what a real backend does differently — emit
  interim then final utterances with the right transcript, and finalize on silence.
  This is the `ASRModule` contract, observed through `ASREngine`. Adding a module =
  adding one row to the `MODULES` table in `helpers.py`; run one backend with
  `pytest -k <module>` (the param id is the module name).
- **Single provider** (module-agnostic): everything downstream of
  `SpeechUtterance`/`SpeechSegment` — the MCP resource/tool surface and the
  asr-to-terminal bridge. These consume events regardless of which module produced
  them, so running them against every module would only re-test the same adapter code
  over the network. The `Segmenter`'s three modes are already covered deterministically
  in the fast tier (`tests/test_segmenter.py`), so the e2e layer does not re-run that
  matrix per module. The provider is chosen in one place — `helpers.default_provider()`.

**Test naming:** functions are named for the behavior under test, never the module
(the folder already says "e2e"; the module identity shows up only as a parametrize
id). So `test_resource_emits_final_transcript`, not `test_e2e_deepgram_v1`.

**Engine-direct, per module** (`test_engine_modules.py`) — the `ASREngine` on its own,
the way a program that `import asr_engine` uses it, no MCP server:

```
FileAudioSource → asyncio.Queue → ASRModule (Deepgram API)
                                         │
                                    ASREngine (utterance/segment callbacks, listen)
                                         │
                                  collected results → assertion
```

Built with the `build_engine(...)`/`build_file_engine(...)` helpers (in `helpers.py`),
which wire an audio source to an `ASREngine` (sound feedback disabled). Parametrized
over `MODULES`; three tests per module: a two-utterance streaming test (via
`ScriptableAudioSource`) asserting interim+final utterances and interim+final segments,
plus the `listen` primitive in both modes (trigger_word + timeout).

**Through MCP, single provider** (`test_mcp_resource.py`, `test_mcp_tools.py`,
`test_asr_to_terminal.py`) — the same pipeline surfaced over the server:

```
FileAudioSource → asyncio.Queue → ASRModule (Deepgram API)
                                         │
                                    ASREngine (utterance/segment callbacks)
                                         │
                                  MCP server (subprocess / in-process)
                                         │
                                  MCP client (in-process)
                                         │
                                  collected results → assertion
```

Unit tests for individual components are out of scope here.

## Audio Source Abstraction

`AudioCapture` (microphone) and the new `FileAudioSource` share the same interface:

| Method | Behaviour |
|--------|-----------|
| `start() -> asyncio.Queue[bytes]` | Open source, return queue |
| `stop() -> None` | Close source |

`ASREngine` accepts an optional `audio_source` at construction time. When provided
it is used instead of constructing an `AudioCapture` from config.

`run_server` selects the source from config: when `audio.audio_file` is set it
builds a `FileAudioSource(audio_file, trailing_silence_s=audio.trailing_silence_s)`
instead of a live `AudioCapture` (see [configuration.md](configuration.md)). This
is how the subprocess-based e2e tests drive the real `asr-engine-mcp` binary from
a WAV fixture without a microphone — `tests-e2e/helpers.start_mcp_server` writes a
temp config with those two fields.

### `ScriptableAudioSource` — hand-sequenced playback

`FileAudioSource` plays one file, front to back, the moment the engine starts —
it fixes the whole audio timeline up front. `ScriptableAudioSource` (in
`audio.py`, the third `AudioSource` implementation) instead lets a test **decide
at runtime when each utterance is spoken**, which is what a developer building on
`import asr_engine` needs to script a multi-utterance scenario:

- Once `start()`ed it feeds **digital silence** into the queue at real-time
  cadence, so a streaming backend stays connected and finalizes utterances on the
  gaps between them.
- `await play(path, *, trailing_silence_s=0.0)` feeds that WAV's audio followed by
  the trailing silence, then silence resumes. It **resolves once the audio has
  been fed onto the queue**, not once the backend has transcribed it — the source
  can't see the ASR's state, so the test then waits on the engine's own
  utterance/segment output. The trailing silence is what prompts the backend to
  finalize the just-played utterance. Queued/concurrent `play` calls run in order.
- It validates each file against the audio contract (16 kHz mono s16), raising
  `ValueError` on a mismatch and `RuntimeError` if `play` is called before
  `start`.
- `real_time=False` drops the per-chunk pacing sleep (feeding as fast as the
  consumer drains) so the source's own mechanics can be unit-tested deterministically
  and fast, off the network, in `tests/test_audio.py`.

Because playback is driven by method calls rather than config, this source is for
**in-process / direct-engine** tests (via `ASREngine(config, audio_source=...)` or
the `build_engine` helper) — not the config-driven subprocess server path, which
stays with `FileAudioSource`. The per-module `test_engine_streams` scenario uses it to
play two fixtures one after the other and assert interim+final utterances, an interim
(open) segment, and a distinct closed final segment for each.

## Audio Fixture

- **Format:** WAV, 16 kHz, mono, signed 16-bit PCM (matches pipeline constants in
  `audio.py`)
- **Location:** `tests-e2e/fixtures/sample.wav`
- **Content:** a short utterance whose transcript is known in advance
- **Expected transcript constant:** `"the sky is blue"` (lowercase, no punctuation)
- **Playback rate:** real-time (one 100 ms chunk every 100 ms) so the streaming
  API receives audio at a natural cadence

The fixture file is not generated by the test suite. It is produced once with an
online TTS tool and committed to the repository.

## Test Cases

### Per module — `test_engine_modules.py`

Parametrized over the `MODULES` table in `helpers.py`. Each row carries the module
type, model, a per-module `silence_s` (the gap the backend needs to finalize an
utterance — Flux/`EndOfTurn` needs longer than nova-3/`is_final`), and `api_key_env`:

| Module | Model | `silence_s` |
|--------|-------|-------------|
| `deepgram_v1` | `nova-3` | `3.0` |
| `deepgram_v2` | `flux-general-en` | `6.0` |

Three tests run for each row:

- `test_engine_streams` — `ScriptableAudioSource`, `utterance` mode. Plays two fixtures
  split by `silence_s`; asserts an interim utterance, a final utterance with the right
  transcript, an interim (open) segment, and ≥2 closed segments (`end_reason ==
  "utterance"`), the second containing `"validate"`.
- `test_listen_trigger_word` — `engine.listen(mode="trigger_word")` on
  `sample_submit.wav`; asserts `end_reason == "trigger_word"`, transcript excludes the
  trigger, engine stopped.
- `test_listen_timeout` — `engine.listen(mode="timeout")` on `sample.wav`; asserts
  `end_reason == "end_of_speech_timeout"` and the full transcript.

### Single provider — module-agnostic stack

Built from `helpers.default_provider()` (`deepgram_v1`/`nova-3`); one run each, since
they exercise adapter/bridge code that is independent of the backend:

| Test file | Covers | Port(s) |
|-----------|--------|---------|
| `test_mcp_resource.py` | `asr://utterance` resource yields a final transcript | `18001` |
| `test_mcp_tools.py` | `listen` tool: trigger_word, timeout, streaming progress | `18003`–`18005` |
| `test_asr_to_terminal.py` | asr-to-terminal bridge: typing, submit, timeout | `18101`–`18103` |

All tests share the same fixtures (`sample.wav` → `"the sky is blue"`,
`sample_submit.wav` → `"the sky is blue validate"`) and the same normalized
transcript comparison.

## Assertion Strategy

1. At least one result with `is_final=True` must be received.
2. The transcript of the last final result is normalised before comparison:
   - converted to lowercase
   - all characters that are not `[a-z0-9 ]` are removed
   - leading/trailing whitespace stripped
3. The normalised transcript must equal the normalised expected constant.

This tolerates differences in punctuation and capitalisation across models.

## Infrastructure

- **Engine-direct tests** (`test_engine_modules.py`): no server. `helpers.build_engine`
  / `build_file_engine` construct an `ASREngine` in-process around an audio source
  (`ScriptableAudioSource` or `FileAudioSource`) and consume its utterance/segment
  callbacks and `listen` directly.
- **Through-MCP tests** (`test_mcp_resource.py`, `test_mcp_tools.py`,
  `test_asr_to_terminal.py`): `helpers.start_mcp_server` spawns a real
  `asr-engine-mcp` **subprocess** (`uv run asr-engine-mcp --config <temp>`) on a
  dedicated port, writing a temp JSON config with `audio.audio_file`, the `module`
  block, and any `engine` overrides. It waits until the TCP port accepts connections
  (`_wait_for_port`); `stop_mcp_server` terminates the process and removes the temp
  config.
- **Clients:** `AsrResourceClient` (subscribes to `asr://utterance`) for the resource
  path, `McpToolClient` (single tool call, optional progress callback) for the `listen`
  tool, and `AsrToTerminal` with an injected in-memory `RecordingTyper` for the
  terminal bridge.
- **Timeout:** 30 seconds per test (covers real-time audio playback + API
  round-trip).
- **API key:** tests never read the literal key. Each module config carries
  `api_key_env` — the *name* of the env var it authenticates with (Deepgram's is the
  `DEEPGRAM_API_KEY_ENV` constant; other modules name their own, a keyless module names
  none) — and the module's own `resolve_api_key` reads it, so no secret is committed.
  `default_provider()` and the `MODULES` rows set `api_key_env`;
  `helpers.require_api_key(module_config)` **skips** (via `pytest.skip`) when the env
  var *that config names* is unset, keeping e2e opt-in — without it `resolve_api_key`
  would raise and fail the test. A config without `api_key_env` is never skipped. See
  AGENTS.md "Live/e2e tests".

## Non-Goals

- Mocking the Deepgram API — the test must hit the real backend.
- Testing pause/resume/reconnection logic.
- Running in CI without a real API key.
