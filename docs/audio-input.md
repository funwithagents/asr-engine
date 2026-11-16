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

The file is read at its own sample rate and channel count, and converted when the module takes another format ([below](#audio-format-and-conversion)). Setting `audio.sample_rate` or `audio.channels` with a file is a check: a file that differs is refused. `trailing_silence_s` appends silence after the file, which gives a remote backend the pause it needs to finalize the last utterance.

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
- Each item is a chunk of raw PCM **in `engine.audio_format`**. By default that is the module's format, and the source produces it. When your stream has a format of its own, state it in the `audio` block (`sample_rate`, `channels`, `encoding`) and the engine converts it for the module ([below](#audio-format-and-conversion)). `engine.audio_format` is valid right after construction, before `start()`, so a source can read it to know what to produce.
- Any reasonable chunk size works.

The bundled sources follow the same protocol: `AudioCapture` (the microphone), `FileAudioSource`, and `ScriptableAudioSource`, which feeds silence and plays files on demand for tests ([testing-your-application.md](testing-your-application.md)). The full contract is in the [audio-source specification](../specs/audio-source.md).

## Audio format and conversion

The engine keeps two formats and converts between them when they differ:

- the **source format**, `engine.audio_format`: what the audio source produces;
- the **module format**, `engine.module_audio_format`: what the ASR module is fed.

You rarely need to set either. With an empty `audio` block the microphone is opened at the module's format, and a file is read at its own. When the source's format is not one the module takes, the engine resamples it, mixes it down to mono and re-encodes it as needed, and logs one line saying so.

| Field | Describes | When unset |
|---|---|---|
| `audio.sample_rate`, `audio.channels` | What the source produces | Microphone or injected source: the module's. File: the file's own |
| `audio.encoding` | The source's encoding, `linear16` or `mulaw` | `linear16` |
| `module.sample_rate` | The rate the module is fed | The source's rate when the module supports it, otherwise the module's default |

Set `module.sample_rate` to feed a module another rate than the engine would pick. It must be one the module supports. For example, a 48 kHz microphone sending 16 kHz to Deepgram:

```json
{
  "audio": { "sample_rate": 48000 },
  "module": {
    "type": "deepgram-v1",
    "api_key_env": "DEEPGRAM_API_KEY",
    "sample_rate": 16000
  }
}
```

The rates and encodings each module takes, with its default rate first:

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
