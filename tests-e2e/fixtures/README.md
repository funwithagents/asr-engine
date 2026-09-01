# E2E Test Fixtures

Short spoken-utterance audio files used by the end-to-end ASR tests. Each file is
produced once with an online TTS tool and committed to the repository — the test
suite does **not** generate them.

## Naming convention

```
sample_<sample_rate>_<transcript>.<ext>
```

- `<sample_rate>` — the file's sample rate in Hz (e.g. `16000`, `44100`).
- `<transcript>` — the spoken words with spaces removed, so the expected
  transcript is recoverable from the name (`theskyisblue` → "the sky is blue").
- `<ext>` — container/encoding: `wav` (16-bit signed PCM, mono) or `mp3`.

A file's audio format is therefore fully described by its name. Because file
sources are **validated, not resampled** (see
[configuration.md](../../specs/configuration.md)), a fixture must be played
through an engine configured with a matching `sample_rate` / `encoding`.

## Files

| File | Sample rate | Container | Expected transcript |
|---|---|---|---|
| `sample_16000_theskyisblue.wav` | 16 000 Hz | WAV / s16 mono | `the sky is blue` |
| `sample_16000_theskyisbluevalidate.wav` | 16 000 Hz | WAV / s16 mono | `the sky is blue validate` |
| `sample_44100_theskyisblue.mp3` | 44 100 Hz | MP3 | `the sky is blue` |
| `sample_44100_theskyisbluevalidate.mp3` | 44 100 Hz | MP3 | `the sky is blue validate` |
| `sample_44100_hellohowareyoutoday.mp3` | 44 100 Hz | MP3 | `hello how are you today` |
| `sample_44100_Iamveryhappytomeetyou.mp3` | 44 100 Hz | MP3 | `I am very happy to meet you` |
| `sample_44100_letsvalidatethis.mp3` | 44 100 Hz | MP3 | `let's validate this` |

The `...validate` fixtures end with the trigger word **`validate`**, which closes
a segment in `trigger_word` mode (and fires Enter injection in the
`AsrToTerminal` state machine) instead of plain text typing.
