# Choosing a module

Every module plugs into the same engine, so the choice is about the backend: where it runs, what it costs, which languages it covers and how it decides that an utterance is over. Changing your mind later means changing the `module` block, not your code.

## Start from your constraint

| If you need… | Use | Why |
|---|---|---|
| nothing to leave the machine, on an Apple Silicon Mac | [`mlx-audio`](modules/mlx-audio.md) | Local, multilingual with the default Parakeet model, very fast decodes |
| nothing to leave the machine, on Linux or Windows | [`kyutai`](modules/kyutai.md), or [`openai-transcriptions-compatible`](modules/openai-transcriptions-compatible.md) pointed at a local server | Kyutai runs in process (English and French; its PyTorch backend is not yet verified). A local server covers more languages and models: [local-speech-recognition.md](local-speech-recognition.md) lists the solutions |
| a quick start with a hosted API, many languages | [`deepgram-v1`](modules/deepgram.md) or [`mistral-realtime`](modules/mistral.md) | Streaming, multilingual with automatic detection, one API key |
| an English voice agent that knows when the speaker has finished | [`deepgram-v2`](modules/deepgram.md) | Flux decides the end of a turn from context, not only from silence |
| OpenAI's transcription models | [`openai-realtime-compatible`](modules/openai-realtime-compatible.md) for streaming, [`openai-transcriptions-compatible`](modules/openai-transcriptions-compatible.md) for one request per utterance | Same models, two protocols |
| a model served by your own gateway (LiteLLM, Dynamo, LocalAI, Groq, …) | whichever of the two OpenAI-compatible modules matches the protocol the server speaks | Both take a `url` |
| to test your application without any backend | [`fake`](modules/fake.md) | Scripted, deterministic, in the base installation |

## What differs between modules

| Module | Runs | Results | Languages | End of utterance | Cost |
|---|---|---|---|---|---|
| `deepgram-v1` | Cloud | Streaming, interims | Many, auto-detected with `multi` | Deepgram's endpointing | Per audio minute |
| `deepgram-v2` | Cloud | Streaming, interims | English | Flux's contextual turn detection | Per audio minute |
| `mistral-realtime` | Cloud | Streaming, append-only interims | 45, auto-detected | Voxtral's end-of-turn events | Per audio minute, silence included |
| `openai-realtime-compatible` | Cloud or your server | Streaming; interims depend on the model | Depends on the model | The server's VAD, or the VAD gate | Per the provider |
| `openai-transcriptions-compatible` | Cloud or your server | One final per utterance; interims off by default | Depends on the model | The VAD gate (required) | Per request on a hosted API |
| `kyutai` | In process | Streaming, interims | English, French | The model's own end-of-turn detection | Free; about 2 GB of weights |
| `mlx-audio` | In process (Apple Silicon) | One final per utterance, plus re-decoded interims | Depends on the model; 25 European languages by default | The VAD gate (required) | Free; weights depend on the model |

## Streaming or batch

A **streaming** module sends audio as it is captured and receives text while the person is still speaking. A **batch** module (`mlx-audio`, `openai-transcriptions-compatible`) waits for the end of the utterance, then transcribes it in one go.

- A batch module needs the [VAD gate](vad.md), which tells it where the utterance ends. A streaming one works without it.
- A batch module's final arrives one decode after the speaker stops: a few hundredths of a second for a local Parakeet, about a second for a hosted request.
- `mlx-audio` still gives interims, by re-decoding the growing utterance every half second. On a hosted batch endpoint each interim is a billed request, so they are off by default.

## Paying for silence

A hosted streaming backend bills the audio it receives, and without a gate the engine sends everything, silence included. If the engine runs for hours and people speak for minutes, add the [VAD gate](vad.md): only speech is sent. Each module's recommended setting is in [vad.md](vad.md#the-setting-that-suits-each-module).

## Trigger words

If you close segments on a trigger word ([segmentation.md](segmentation.md)), a module that takes vocabulary hints recognizes it more reliably: `context_bias` on `mistral-realtime`, `prompt` on the two OpenAI-compatible modules.
