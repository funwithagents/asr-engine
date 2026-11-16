# Testing your application

Code built on `asr-engine` can be tested without a microphone, a network or an API key. Two pieces of the base installation make that possible:

- the [`fake`](modules/fake.md) module, which replays a transcript you script instead of recognizing speech;
- `ScriptableAudioSource`, an audio source that feeds silence, in real time or as fast as the engine drains it.

Everything downstream of the module is real: the engine, segmentation, `listen`, dictation, `AsrTools` and the MCP server run exactly as they do with a real backend.

## A deterministic test

Script what the "speaker" says, and feed the engine fast silence so that the script replays in milliseconds:

```python
import asyncio

from asr_engine import ASREngine, ASREngineConfig, ScriptableAudioSource


def build_engine() -> ASREngine:
    config = ASREngineConfig.from_dict(
        {
            "audio_source": {"type": "custom"},
            "sound_feedback": {"enabled": False},
            "segmentation": {"trigger_words": ["send"]},
            "module": {
                "type": "fake",
                "utterances": [
                    {"text": "turn on the lights", "start_s": 0.2, "end_s": 1.0},
                    {"text": "send", "start_s": 1.2, "end_s": 1.4},
                ],
            },
        }
    )
    return ASREngine(config, audio_source=ScriptableAudioSource(real_time=False))


def test_the_command_is_heard() -> None:
    segment = asyncio.run(build_engine().listen(mode="trigger_word"))

    assert segment.transcript == "turn on the lights"
    assert segment.end_reason == "trigger_word"
```

What each setting does:

- `"sound_feedback": {"enabled": False}` keeps `listen()` from playing its cues on the test machine.
- `real_time=False` makes the source feed as fast as the engine reads. The `fake` module counts time in audio received, so the 1.4 s script runs at once.
- The script holds the trigger word as its own utterance; it closes the segment and is left out of the transcript, as with a real module.

In your own tests, build the engine the way your application does and inject the source: the code under test receives the same callbacks and return values as in production.

## When to keep real time

The `timeout` segmentation mode runs on wall-clock timers. To test it, keep the default `ScriptableAudioSource()` (real time) and short timeouts in the `segmentation` block.

## Testing with recorded audio

To exercise a real module against a recording, use file input (an `audio_source` of type `file`, [audio-input.md](audio-input.md#a-file)) or `ScriptableAudioSource.play()`, which feeds a WAV file on demand and then resumes silence:

```python
source = ScriptableAudioSource()
engine = ASREngine(config, audio_source=source, on_speech_segment=on_segment)

await engine.start()
await source.play("hello.wav", trailing_silence_s=2.0)
# Wait for the engine's output, then assert on it.
await engine.stop()
```

`play()` returns when the audio has been fed, not when the backend has transcribed it, so wait on the engine's own output. The trailing silence is what lets a streaming backend finalize the utterance. The file must already be in the engine's audio format.

## Testing through the MCP server

The MCP server selects its module from a config file, and `fake` is a registered module, so a server started on a `fake` config behaves like a real one with a known transcript. This repository's own end-to-end tests work this way ([specs/e2e-testing.md](../specs/e2e-testing.md)).
