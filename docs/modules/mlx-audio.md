# `mlx-audio`

**Kind:** local · **Backend:** any mlx-audio STT model, on-device (Apple Silicon) · **Extra:** `mlx-audio` · **Credentials:** none · **VAD gate:** required

[mlx-audio](https://github.com/Blaizzy/mlx-audio) runs about thirty speech-to-text model families on Apple Silicon: Parakeet, Qwen3-ASR, Whisper and more. The model is a config value, and it runs inside the process: no network once the weights are downloaded, no API key.

These are batch models: they transcribe a whole utterance at once, so the module needs the [VAD gate](../vad.md) to know where utterances end. Install the `silero` extra with it:

```bash
uv sync --no-default-groups --extra mlx-audio --extra silero
# For an installed package:
pip install 'asr-engine[mlx-audio,silero]'
```

`mlx-audio` cannot be installed with `kyutai-mlx` or `kyutai-torch` ([why](index.md#installing-a-module)).

```json
{
  "module": {
    "type": "mlx-audio",
    "model": "mlx-community/parakeet-tdt-0.6b-v3"
  },
  "vad": { "type": "silero" }
}
```

| Field | Default | Description |
|---|---|---|
| `model` | `mlx-community/parakeet-tdt-0.6b-v3` | Any mlx-audio STT model on Hugging Face, or a local path. Measured: Parakeet v3, `mlx-community/Qwen3-ASR-0.6B-8bit`, `whisper-large-v3-turbo-asr-4bit` |
| `generate_kwargs` | `{}` | Passed to the model's `generate()`. It differs per family: Whisper takes `{"language": "en"}`, Qwen3-ASR `{"language": "English"}` |
| `interim_interval_s` | `0.5` | Re-decode the growing utterance for an interim after this much more speech; `0` disables interims |
| `max_utterance_s` | `30.0` | Cut a longer utterance into several finals |
| `stop_timeout_s` | `10.0` | How long stopping waits for the last decodes |

## Behaviour

- The gate ends each utterance and the module decodes it: about 0.03 s for Parakeet once warm.
- Interims come from re-decoding the utterance as it grows, so an interim can rewrite earlier words.
- The default, Parakeet v3, covers 25 European languages with automatic detection and needs no `generate_kwargs`.
- Audio is 16000 Hz mono `linear16`, the default.

## Weights

The model is downloaded on first use and stays loaded between `listen` calls. To fetch it ahead of time:

```bash
python -m asr_engine.modules.mlx_audio.download            # the default model
python -m asr_engine.modules.mlx_audio.download <model>    # another one
```

## Troubleshooting

- `needs a VAD to know when an utterance ends`: add `"vad": {"type": "silero"}` to the engine block and install the `silero` extra.
- An `ImportError` naming the `mlx-audio` extra: it is not installed, or the machine is not Apple Silicon.
- A model rejects its arguments: `generate_kwargs` are specific to each family. Start with `{}` and add only what the model's page documents.

## See also

- [specs/modules/mlx-audio.md](../../specs/modules/mlx-audio.md): the module's contract and the models measured.
- [specs/batch-asr-module.md](../../specs/batch-asr-module.md): how batch modules buffer, decode and produce interims.
- [examples/configs/mlx-audio.json](../../examples/configs/mlx-audio.json): a complete server config.
- [`kyutai`](kyutai.md): the other local module, streaming, for English and French.
