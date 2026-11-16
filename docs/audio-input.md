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
    "trailing_silence_s": 2.0
  },
  "module": {
    "type": "deepgram-v1",
    "api_key_env": "DEEPGRAM_API_KEY"
  }
}
```

The file is read at its own sample rate and channel count, and converted to the module's format when they differ ([below](#audio-format-and-conversion)). `trailing_silence_s` appends silence after the file, which gives a remote backend the pause it needs to finalize the last utterance.

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
- Each item is a chunk of raw PCM **in `engine.audio_format`**. With no `audio.audio_format`, that is the format the module takes, which differs between modules (16000 Hz for most, 24000 Hz for `kyutai` and `openai-realtime-compatible`): read `engine.audio_format`, valid right after construction, rather than assume a rate. When your stream has a fixed format, state it in `audio.audio_format` instead, and the engine converts it for the module ([below](#audio-format-and-conversion)).
- Any reasonable chunk size works.

The bundled sources follow the same protocol: `AudioCapture` (the microphone), `FileAudioSource`, and `ScriptableAudioSource`, which feeds silence and plays files on demand for tests ([testing-your-application.md](testing-your-application.md)). The full contract is in the [audio-source specification](../specs/audio-source.md).

## Audio format and conversion

The engine keeps two formats and converts between them when they differ:

- the **source format**, `engine.audio_format`: what the audio source produces;
- the **module format**, `engine.module_audio_format`: what the ASR module is fed.

Each is set by an `audio_format` object with three optional fields, `sample_rate`, `channels` and `encoding` (`linear16` or `mulaw`):

| Object | Sets | A field left unset |
|---|---|---|
| `module.audio_format` | What the module is fed | The module's own default |
| `audio.audio_format` | What the source produces | The same as the module. For a file: the file's own rate and channel count |

Most configurations need neither. With nothing set, the microphone is opened at the format the module takes and nothing is converted; a file is read at its own rate and converted when the module takes another. The engine logs the formats in use each time it starts.

The module's format never depends on the source. A field you set in `module.audio_format` must be a value the module supports, otherwise engine construction fails with the supported values.

Two examples. A 48 kHz capture, which the engine resamples to Deepgram's 16 kHz default:

```json
{
  "audio": { "audio_format": { "sample_rate": 48000 } },
  "module": { "type": "deepgram-v1", "api_key_env": "DEEPGRAM_API_KEY" }
}
```

A 44.1 kHz file sent to Deepgram as it is, instead of resampled to 16 kHz:

```json
{
  "audio": { "audio_file": "speech.mp3" },
  "module": {
    "type": "deepgram-v1",
    "api_key_env": "DEEPGRAM_API_KEY",
    "audio_format": { "sample_rate": 44100 }
  }
}
```

The rates and encodings each module takes. The default rate is 16000, except for `kyutai` and `openai-realtime-compatible` (24000); the default encoding is `linear16`:

| Module | Sample rates (Hz) | Encodings |
|---|---|---|
| `deepgram-v1`, `deepgram-v2` | 8000, 16000, 24000, 44100, 48000 | `linear16`, `mulaw` |
| `mistral-realtime` | 16000, 24000, 44100 | `linear16` |
| `openai-realtime-compatible` | 24000 only | `linear16` |
| `openai-transcriptions-compatible`, `mlx-audio` | 16000 | `linear16` |
| `kyutai` | 24000 only | `linear16` |
| `fake` | any | any |

All modules take mono audio. `openai-realtime-compatible` lists hosted OpenAI's rate; its `supported_sample_rates` and `default_sample_rate` fields describe a server that takes others ([modules/openai-realtime-compatible.md](modules/openai-realtime-compatible.md)).

Resampling delays the audio by 5 to 30 ms for the usual rate pairs. A stereo source is averaged to mono; a layout where neither side is mono cannot be converted and fails at construction.
