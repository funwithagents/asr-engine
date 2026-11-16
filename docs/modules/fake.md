# `fake`

**Kind:** test double · **Backend:** replays a scripted transcript; no recognition · **Extra:** none, it is in the base installation · **Credentials:** none · **VAD gate:** optional

`fake` is a scripted module for **tests and demos only**. It ignores the content of the audio and replays the utterances you configure, so code built on `asr-engine` can be exercised deterministically, with no network and no key. Do not use it for speech recognition: it recognizes nothing.

```json
{
  "module": {
    "type": "fake",
    "utterances": [
      {"text": "the sky is blue", "start_s": 0.5, "end_s": 1.5},
      {"text": "send", "start_s": 3.0, "end_s": 3.4, "confidence": 0.9}
    ]
  }
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `utterances` | no | `[]` | The script. An empty script is valid: the module connects and emits nothing |
| `utterances[].text` | yes | — | The transcript of this utterance, emitted as is in its final |
| `utterances[].start_s` | yes | — | When the utterance starts, in seconds of audio |
| `utterances[].end_s` | yes | — | When the utterance ends and its final is emitted |
| `utterances[].confidence` | no | `null` | Carried on every result of this utterance |

Utterances must be in order and must not overlap.

## What it emits

For each utterance the module emits its words one by one as interims, evenly spaced over `[start_s, end_s]`, then the full text as a final at `end_s`. For `{"text": "the sky is blue", "start_s": 1.0, "end_s": 2.0}`:

| Time (s) | Transcript | Final |
|---|---|---|
| 1.25 | `the` | no |
| 1.50 | `the sky` | no |
| 1.75 | `the sky is` | no |
| 2.00 | `the sky is blue` | yes |

## Its clock is the audio

Time is measured in audio received, not on the wall clock. Two consequences:

- **The script only advances while audio flows.** With a microphone or a file it replays at natural speed. If the source stops feeding, the remaining events are never emitted.
- **A fast source replays it instantly.** `ScriptableAudioSource(real_time=False)` feeds silence as fast as the engine drains it, so a multi-second script runs in milliseconds in a test.

Each `start()` replays the script from the beginning, so each `listen()` hears it again.

## See also

- [testing-your-application.md](../testing-your-application.md): using `fake` to test code built on the engine.
- [specs/modules/fake.md](../../specs/modules/fake.md): the module's contract, including validation and tick resolution.
