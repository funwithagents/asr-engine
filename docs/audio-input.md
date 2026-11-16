# Audio input and format

The engine reads audio from one of three places: an input device (the default), a file, or a source your application provides.

## The microphone

By default the engine captures from `audio.device`. `null` selects the system's default input; a name selects a device, and an unknown name fails with the list of available devices.

```json
{
  "audio": { "device": null }
}
```

On Linux, live capture needs the system PortAudio (`libportaudio2` on Debian and Ubuntu). The macOS and Windows wheels of `sounddevice` bundle it.

## A file

Set `audio.audio_file` to stream a decoded audio file (WAV, MP3, …) at real-time pace instead of using an input device:

```json
{
  "audio": {
    "audio_file": "speech.mp3",
    "trailing_silence_s": 2.0,
    "sample_rate": 44100,
    "channels": 1,
    "encoding": "linear16"
  },
  "module": {
    "type": "deepgram-v1",
    "api_key_env": "DEEPGRAM_API_KEY"
  }
}
```

The file is decoded and checked against the resolved sample rate and channel count; it is **not resampled**, so `audio.sample_rate` must be the file's own rate. `trailing_silence_s` appends silence after the file, which gives a remote backend the pause it needs to finalize the last utterance.

## Injecting your own source

Pass an `AudioSource` to the engine to feed it audio your application already has, such as a stream from a robot's microphone or a telephony bridge. An injected source takes precedence over `audio.audio_file` and the input device. A source is any object with these two methods:

```python
import asyncio

from asr_engine import ASREngine


class QueueAudioSource:
    """Feeds the engine the PCM chunks the application pushes."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    def start(self) -> asyncio.Queue[bytes]:
        self._queue = asyncio.Queue()
        return self._queue

    def stop(self) -> None:
        pass

    def push(self, chunk: bytes) -> None:
        self._queue.put_nowait(chunk)


source = QueueAudioSource()
engine = ASREngine(config, audio_source=source)
```

The contract in brief:

- `start()` returns the queue the source owns and feeds; `stop()` halts feeding. The engine may call `start()` again later, so a source must be restartable.
- Each item is a chunk of raw PCM **already in `engine.audio_format`**. The engine does not resample or re-encode what an injected source provides. `engine.audio_format` is valid right after construction, before `start()`, so a source can read it to know what to produce.
- Any reasonable chunk size works.

The bundled sources follow the same protocol: `AudioCapture` (the microphone), `FileAudioSource`, and `ScriptableAudioSource`, which feeds silence and plays files on demand for tests ([testing-your-application.md](testing-your-application.md)). The full contract is in the [audio-source specification](../specs/audio-source.md).

## Audio-format negotiation

`audio.sample_rate`, `audio.channels` and `audio.encoding` describe the format the engine should deliver to the module. The defaults are 16000 Hz, mono, `linear16`. Every module declares the values it supports and a default for each.

| `on_unsupported_format` | When a requested value is unsupported |
|---|---|
| `error` (default) | Engine construction fails with a message listing the module's supported values |
| `fallback` | That value is replaced by the module's default and a warning is logged |

The resolved format is `engine.audio_format`, and both the audio source and the module receive it.

| Module | Sample rates (Hz) | Encodings |
|---|---|---|
| `deepgram-v1`, `deepgram-v2` | 8000, 16000, 24000, 44100, 48000 | `linear16`, `mulaw` |
| `mistral-realtime` | 16000, 24000, 44100 | `linear16` |
| `openai-realtime-compatible` | 24000 only | `linear16` |
| `openai-transcriptions-compatible`, `mlx-audio` | 16000 | `linear16` |
| `kyutai` | 24000 only | `linear16` |
| `fake` | any | any |

All modules take mono audio. Two modules need a rate other than the default: set `audio.sample_rate` to `24000` for `kyutai` and for `openai-realtime-compatible`.
