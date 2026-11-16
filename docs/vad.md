# Voice activity detection

By default the engine streams everything it captures to the ASR module, silence included. A hosted backend bills that silence, and a local model keeps computing on it. An optional `vad` block puts a speech gate in front of the module: only speech is forwarded, with a short pre-roll so the first word is not clipped, and when the speaker stops the module is told to emit its final.

Two modules cannot work without it: `mlx-audio` and `openai-transcriptions-compatible` are batch modules, and the gate is what tells them an utterance has ended.

## Enabling the gate

Install the `silero` extra next to your module's:

```bash
uv sync --no-default-groups --extra deepgram --extra silero
# For an installed package:
pip install 'asr-engine[deepgram,silero]'
```

Then add a `vad` block to the engine block:

```json
{
  "module": { "type": "deepgram-v1", "api_key_env": "DEEPGRAM_API_KEY" },
  "vad": { "type": "silero" }
}
```

The bundled VAD is [Silero](https://github.com/snakers4/silero-vad), run with `onnxruntime`, without PyTorch. Its model (2.3 MB) is downloaded on first use from a pinned upstream release, checked against a known hash, and kept in the user cache (`~/.cache/asr-engine`, or `$ASR_ENGINE_CACHE_DIR`). On a machine without network access, download the file yourself and set `model_path`. The gate works with any audio format the ASR module accepts.

## Settings

| Field | Default | Purpose |
|---|---|---|
| `type` | — | VAD to load. `"silero"` is built in (the `silero` extra) |
| `threshold` | `0.5` | Speech probability at or above which audio counts as speech |
| `min_speech_duration_ms` | `250` | Speech needed before the gate opens, so a click does not open it |
| `min_silence_duration_ms` | `500` | Silence needed before the gate closes and the final is requested |
| `speech_pad_ms` | `300` | Audio kept from before the detected start of speech |
| `model_path` | — | (`silero`) Path of a local copy of the model, used instead of the download |

## The setting that suits each module

Every bundled module works behind the gate. The right setting depends on whether the backend has its own turn detection:

| Module | Recommended `vad` block | Why |
|---|---|---|
| `deepgram-v1`, `mistral-realtime`, `openai-realtime-compatible` | `{"type": "silero"}` | The gate ends each utterance, and the bill follows speech time |
| `deepgram-v2` (Flux) | `{"type": "silero", "min_silence_duration_ms": 1500}` | The longer window lets Flux hear enough silence to end the turn by its own contextual judgement; the gate then only removes the idle time between utterances |
| `mlx-audio`, `openai-transcriptions-compatible` | `{"type": "silero"}` (required) | A batch model has no end-of-utterance signal of its own; the gate provides it |
| `kyutai` | Usually none; otherwise as for Flux | Local and free, with its own end-of-turn detection; gating only saves compute |

The configs under [examples/config/](../examples/config/) carry these settings.

## What changes with the gate on

- The boundaries of final utterances follow `min_silence_duration_ms`, unless the backend ends the turn first. A larger value gives fewer, longer finals.
- [Segmentation](segmentation.md) is unchanged: `trigger_word` and `timeout` still work on the module's utterances.
- `engine.status()["vad"]` names the configured VAD, and `engine.vad_state` says whether speech is reaching the module right now.

## Limits

`onnxruntime` publishes no Intel macOS wheel, so the Silero VAD is unavailable on that platform.

The design, including how a module declares its relation to the gate, is in the [VAD specification](../specs/vad.md).
