# End-of-Utterance Detector Specification

## Purpose

`EndOfUtteranceDetector` is a shared component that accumulates ASR final results
and signals when the current utterance is complete. It is used by:

- The `listen` MCP tool (`server.py`) — to return a single accumulated transcript
  after end-of-utterance is detected.
- `AsrToTerminal` (`asr_to_terminal.py`) — to know when to send Enter and start
  a fresh utterance cycle.

---

## Data types

### `UtteranceResult`

```python
@dataclass
class UtteranceResult:
    transcript: str    # space-joined committed finals
    end_reason: str    # see values below
```

| `end_reason` value          | When it fires |
|-----------------------------|---------------|
| `"trigger_word"`            | A final result contains a trigger word (`trigger_word` mode only) |
| `"end_of_speech_timeout"`   | Silence after speech exceeded `end_of_speech_timeout_s` (`timeout` mode) |
| `"initial_silence_timeout"` | No ASR event arrived within `initial_silence_timeout_s` (`timeout` mode) |

---

## `EndOfUtteranceDetector`

### Constructor

```python
class EndOfUtteranceDetector:
    def __init__(
        self,
        mode: str,                          # "trigger_word" or "timeout"
        trigger_words: list[str],
        initial_silence_timeout_s: float,   # timeout mode only
        end_of_speech_timeout_s: float,     # timeout mode only
        on_final_committed: Callable[[str], Awaitable[None]] | None = None,
    ) -> None: ...
```

| Parameter | Description |
|---|---|
| `mode` | `"trigger_word"` or `"timeout"` |
| `trigger_words` | Words that end the session (trigger_word mode); ignored in timeout mode |
| `initial_silence_timeout_s` | Seconds of initial silence before giving up (timeout mode) |
| `end_of_speech_timeout_s` | Seconds of silence after speech before committing (timeout mode) |
| `on_final_committed` | Optional async callback, called after each final is appended to `_committed`. Receives the space-joined committed transcript so far. Used for streaming progress (e.g. listen tool). |

### `async on_result(result: ASRResult) -> None`

Feed an ASR result into the session. Called for every interim and final result.

**Behaviour:**

- **Both modes — any event (interim or final):** reset the end-of-speech timer
  (cancel + reschedule).
- **`trigger_word` mode — final with trigger word:** set `end_reason="trigger_word"`,
  signal done. The trigger word utterance is **not** appended to `_committed`.
- **`trigger_word` mode — final without trigger word:** append `result.transcript`
  to `_committed`; call `on_final_committed` if set.
- **`timeout` mode — any final:** append `result.transcript` to `_committed`;
  call `on_final_committed` if set. Trigger words are **not** checked.
- **Interim:** no accumulation; timer reset only.

### `async wait() -> UtteranceResult`

Block until the session is done and return the result.

**Behaviour:**

- **`timeout` mode:** start the initial-silence timer concurrently, then await
  the done event. The end-of-speech timer is managed inside `on_result`.
- **`trigger_word` mode:** simply await the done event.
- Once done: cancel any running timer tasks.
- Return `UtteranceResult` with `transcript = " ".join(_committed)`.

---

## Mode semantics

### `trigger_word` mode

The session ends as soon as a final result contains one of the `trigger_words`
(case-insensitive substring match via `speech_utils.contains_trigger_word`).
Finals without a trigger word are accumulated. The trigger word utterance itself
is not included in the returned transcript.

Timers are not used in this mode.

### `timeout` mode

Two timers operate concurrently:

| Timer | Starts when | Fires after | `end_reason` |
|---|---|---|---|
| Initial-silence | `wait()` called | `initial_silence_timeout_s` with no event | `"initial_silence_timeout"` |
| End-of-speech | First ASR event | `end_of_speech_timeout_s` of silence | `"end_of_speech_timeout"` |

Every ASR event (interim or final) resets the end-of-speech timer. Whichever
timer fires first ends the session.

---

## File location

```
src/asr_mcp/end_of_utterance_detector.py   # EndOfUtteranceDetector + UtteranceResult
```

---

## Usage by `listen` tool

```python
session = EndOfUtteranceDetector(
    mode=listen_config.end_of_utterance_mode,
    trigger_words=listen_config.trigger_words,
    initial_silence_timeout_s=listen_config.initial_silence_timeout_s,
    end_of_speech_timeout_s=listen_config.end_of_speech_timeout_s,
    on_final_committed=_on_final_committed,   # streams progress notifications
)
engine._on_result = session.on_result
await engine.start()
result = await session.wait()
# returns {"transcript": result.transcript, "end_reason": result.end_reason}
```

## Usage by `AsrToTerminal`

`AsrToTerminal` runs a background `_session_loop` that continuously creates new
`EndOfUtteranceDetector` instances (one per utterance). On each session end it
sends Enter and starts a fresh session.

Interim events are handled directly by `AsrToTerminal` for progressive typing;
all events (interim + final) are also fed to the current session via `on_result`
so that timers stay in sync.

See [asr-to-terminal.md](asr-to-terminal.md) for full details.
