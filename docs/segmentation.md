# Segmentation

An ASR module emits utterances; the engine groups them into segments. Segmentation belongs to the engine, so a direct caller, the tools and the MCP resources all see the same speech boundaries, whichever module is used.

## The three modes

| Mode | A segment closes when | Final `end_reason` |
|---|---|---|
| `utterance` | Each final utterance arrives | `utterance` |
| `trigger_word` | A final utterance contains a configured trigger word | `trigger_word` |
| `timeout` | No speech starts within the initial-silence timeout, or speech stops for the end-of-speech timeout | `initial_silence_timeout` or `end_of_speech_timeout` |

- **`trigger_word`**: matching is a case-insensitive substring check. The utterance holding the trigger word closes the segment and is left out of its transcript.
- **`timeout`**: the initial-silence timer starts with the segment, and the end-of-speech timer starts at the first event. Every interim or final event resets the end-of-speech timer.

## Which mode is active

A continuous engine (`start()`) always begins in `utterance` mode. Aggregation is switched on explicitly:

- by `listen(mode=...)`, for one segment;
- by a dictation session, `start_dictation(segmentation_mode=...)`;
- by a hosting application applying `auto_start_dictation` (the MCP server does).

When no mode is given, `listen()` uses `listen_default_segmentation_mode` and a dictation uses `dictation_default_segmentation_mode`. Both default to `trigger_word`. See [running-the-engine.md](running-the-engine.md).

## Settings

Listen and dictation each select a mode, and share the values of the `segmentation` block:

```json
{
  "segmentation": {
    "trigger_words": ["send", "submit"],
    "initial_silence_timeout_s": 10.0,
    "end_of_speech_timeout_s": 5.0
  }
}
```

| Setting | Used by | Meaning |
|---|---|---|
| `trigger_words` | `trigger_word` | Words or phrases that close a segment |
| `initial_silence_timeout_s` | `timeout` | How long to wait for the first speech before closing an empty segment |
| `end_of_speech_timeout_s` | `timeout` | How long without any event before the segment closes |

Change them on a live engine with `await engine.set_segmentation_params(...)`.

## With a VAD gate

A [VAD gate](vad.md) changes where final utterances end, not how segments are built: `trigger_word` and `timeout` still work on the module's utterances. A hosted module that accepts vocabulary hints (`context_bias` on [Mistral](modules/mistral.md), `prompt` on the OpenAI modules) recognizes your trigger words more reliably when you list them there.

The rules in full are in the [engine specification](../specs/engine.md).
