---
code:
  - src/asr_engine/setup.py
  - src/asr_engine/errors.py
  - src/asr_engine/modules/fields.py
  - src/asr_engine/modules/base.py
  - src/asr_engine/modules/__init__.py
  - src/asr_engine/vad/__init__.py
  - src/asr_engine/config.py
tests:
  - tests/test_setup.py
  - tests/modules/test_fields.py
  - tests/conftest.py
---

# Setup

**Status:** Implemented

## Purpose

Everything an application needs to set the engine up for a person, without knowing any backend: which modules exist, what each one's config looks like, which extras a config needs, which key it reads, what it needs on the machine (model weights), which input devices it can listen on, and whether it works. An application draws a form from it, installs what is missing, and only then builds an `ASREngine`.

Before this spec the same knowledge was spread over each module's constructor, the per-module docs, and the error raised when something was missing. A config could only be checked by constructing the module, which imported its provider library, read its key and, for a local module, resolved its model. This spec makes it available as data, before anything is installed or loaded.

These functions implement the **module contract** of the `module-setup` project (its `specs/module-contract.md`), version 1, which is the reference for the shapes exchanged; this spec says what asr-engine puts in them. The engine does not depend on `module-setup` at runtime: the contract is plain data, and `module-setup` is a dev dependency only, for the conformance check its tests run.

## The `setup` module

`asr_engine.setup` exposes seven functions over the **engine block**, the document `ASREngineConfig.from_dict` takes ([configuration.md](configuration.md)). The MCP server's `server` block is not part of it.

```python
def config_schema() -> dict: ...
def required_extras(config: dict) -> list[str]: ...
def check_config(config: dict) -> list[dict]: ...
def check_setup(config: dict) -> list[dict]: ...
def prepare(
    config: dict, on_progress: Callable[[dict], None] | None = None
) -> None: ...
def options(name: str, config: dict) -> list[dict]: ...
def probe(config: dict, *, secrets: Mapping[str, str] | None = None) -> dict: ...
```

They take and return JSON-compatible data only, are synchronous, and import no provider library at module level: importing `asr_engine.setup` (or `asr_engine`) imports no `deepgram`, `websockets`, `httpx`, `onnxruntime`, `mlx_audio` or `moshi`. `config_schema`, `required_extras` and `check_config` read nothing but the config; `check_setup` also tries the selected modules' imports and reads the environment; `prepare` and `probe` need the extras and may use the network.

Each is the engine's own part (the audio source, segmentation, sound feedback, the scalar settings, the module choice, the VAD gate's parameters) plus the method of the same name on the selected ASR module, then on the selected VAD module.

| Function | Returns |
|---|---|
| `config_schema()` | `{"contract": 1, "distribution": "asr-engine", "title": "Speech recognition", "schema": …}`: the engine block as a JSON Schema (below). The same value on every call |
| `required_extras(config)` | The extras this config needs, as requirement strings: the module's (`asr-engine[deepgram]`), then the VAD's (`asr-engine[silero]`). Reads the `type`s (and kyutai's `backend`) only; never raises; `[]` for an unknown or missing type |
| `check_config(config)` | Every problem that makes the loader or a constructor refuse the config, each `{"path": "/module/model", "message": …}` with a JSON Pointer into the engine block; `[]` when it is accepted |
| `check_setup(config)` | What this machine is missing to run it: each `{"id", "title", "status": "ready"/"missing"/"unknown", "detail", "preparable"}`; `[]` when it needs nothing |
| `prepare(config, on_progress)` | Makes every preparable item ready (downloads the model weights). Blocking, idempotent, raises on failure. `on_progress` receives `{"id", "fraction", "message"}` |
| `options(name, config)` | The choices of a named list, each `{"label", "fields"}`: `"input-devices"` only |
| `probe(config)` | One light real check of the config, `{"ok", "message", "duration_s"}`. Never raises |

## Fields are declared once

A module declares its config fields as data, and its constructor, `check_config` and `config_schema` all read the same declaration, so the form, the check and the constructor cannot drift apart.

`modules/fields.py` provides `Field`, `read_fields`, `field_problems` and `fields_schema`:

- **`Field(name, kind, description, default=None, implied=None, required=False, nullable=True, choices=None, minimum=None, maximum=None, exclusive_minimum=None, allow_empty=False, advanced=False, secret=None, options=None, message=None)`**: `kind` is `"string"`, `"number"`, `"integer"`, `"boolean"`, `"objects"` (a list of objects, its content the module's to check), or several of them. `default` is the value of an absent field, and with one an explicit `null` is refused; without one `null` counts as absent (unless `nullable=False`). `message` replaces the default wording of an invalid value.
- **`read_fields(config, fields, label=, error=)`** returns each field's value (its default when absent; a number as a `float`), raising `error` on the first invalid one: `"<label> '<name>' must be <what>"` (`"Deepgram Nova module 'model' must be a non-empty string"`).
- **`field_problems(config, fields, label=)`** runs the same rules and returns every failure as `(name, message)`.
- **`fields_schema(fields)`** returns the `properties` and `required` of a JSON Schema object, with the annotations `x-advanced`, `x-secret-env` (`secret="env"`) and `x-options`.

The file uses the standard library only and knows nothing about speech recognition: the error type is the caller's. It is shared, unchanged, with the other libraries that implement the contract, and is meant to move to a common package they all depend on.

### The engine block's own fields

`config.py` declares the engine's own parts the same way: `ENGINE_FIELDS` (`auto_start`, `auto_start_dictation`, the two default segmentation modes), `SEGMENTATION_FIELDS`, `SOUND_FEEDBACK_FIELDS`, `AUDIO_FORMAT_FIELDS` (both `audio_format` objects), `INPUT_DEVICE_FIELDS` and `FILE_SOURCE_FIELDS` (the two kinds of `audio_source`), `VAD_GATE_FIELDS` (the gate's parameters) and `SERVER_FIELDS`. What a `Field` cannot state stays a rule beside them: `trigger_words` (a list of strings), an unknown key, a field that does not apply to the source's `type`, `auto_start_dictation` needing `auto_start`, the gate's threshold strictly below 1.

**`engine_problems(engine_block)`** returns every problem of the engine's own part, each `(JSON Pointer, message)`. `ASREngineConfig.from_dict` raises the first as a `ConfigError`, so the loader and `check_config` refuse the same configs with the same words ([configuration.md](configuration.md)).

### On a module

`ASRModule` and `VADModule` both derive from `Declared` (`modules/base.py`), which carries the setup side of a module:

```python
class Declared:
    kind: ClassVar[Literal["hosted", "local", "fixture"]]
    title: ClassVar[str]  # "Deepgram Nova", "Silero"
    extra: ClassVar[str | None]  # "deepgram"; None for the fixture, and kyutai
    fields: ClassVar[tuple[Field, ...]]
    schema_extras: ClassVar[Mapping[str, Mapping]] = {}  # fields a Field cannot state
    noun: ClassVar[str] = "module"  # "VAD" on VADModule

    # The contract, over the module's own block, each with a default:
    @classmethod
    def required_extras(cls, config) -> list[str]: ...
    @classmethod
    def check_config(cls, config) -> list[tuple[str, str]]: ...
    @classmethod
    def check_setup(cls, config) -> list[dict]: ...
    @classmethod
    def prepare(cls, config, on_progress=None) -> None: ...
    @classmethod
    def options(cls, name, config) -> list[dict]: ...
    @classmethod
    def probe(cls, config, secrets=None) -> str: ...

    # What they are built on:
    @classmethod
    def imports(cls, config) -> None: ...
    @classmethod
    def check_files(cls, config) -> list[dict]: ...
    @classmethod
    def validated(cls, config) -> dict: ...
    @classmethod
    def fields_for(cls, config) -> tuple[Field, ...]: ...
    @classmethod
    def config_rules(cls, values, config) -> None: ...
    @classmethod
    def schema(cls) -> dict: ...
```

The module's own block is what the engine hands its constructor: `engine.module` without `type` and `audio_format`, `engine.vad` without `type` and the gate's parameters.

| Member | Default | Overridden by |
|---|---|---|
| `kind`, `title`, `extra`, `fields` | none: declared by every module | every module |
| `schema_extras` | none | `mistral-realtime` (`context_bias`, `turn_detection`), `openai-realtime-compatible` (`turn_detection`, `supported_sample_rates`), `openai-transcriptions-compatible` (`extra_fields`), `mlx-audio` (`generate_kwargs`) |
| `required_extras` | `asr-engine[<extra>]`; `[]` without one | `kyutai` |
| `check_config` | the problems of `fields_for(config)`, a key written in the config, or, when there are none, the `config_rules` failure under the name `""` | none |
| `check_setup` | for each required extra, `imports` tried; then the key; then `check_files` once the extras import | none |
| `prepare` | nothing | `kyutai`, `mlx-audio`, Silero |
| `options` | `[]` | none |
| `probe` | says there is nothing to try (the fixture) | every other module |
| `imports` | nothing | every module with an extra |
| `check_files` | `[]` | `kyutai`, `mlx-audio`, Silero |
| `config_rules` | nothing | the modules with a rule across fields or on a `schema_extras` field |

- The label of a module's messages is `"<title> <noun>"`: `"Deepgram Nova module"`, `"Silero VAD"`.
- **`validated`** is `read_fields` over `fields_for(config)`, then `config_rules`. **It is the first statement of every constructor, before any provider import**, so a shape error is reported whether or not the extra is installed, and what `check_config` accepts is what the constructor accepts. `BatchASRModule` calls it for its subclasses and keeps the values in `self._values`; their fields are their own followed by `batch_fields(<interim default>)`.
- **`imports`** imports what the extra provides, in a `try` raising `ImportError` with the install hint (`"<label> requires the optional dependency '<extra>' which is not installed. Install it with: pip install 'asr-engine[<extra>]' (from source: uv sync --extra <extra>)"`). The constructor calls it before the module first uses its provider; `check_setup` calls it to find out whether the extra is usable. Trying the import, rather than looking the package up, tests what will run. It takes the config because kyutai's depends on its `backend`. A missing extra stays an `ImportError`, as it was before this spec: it is not a config problem.
- **`config_rules`** is the hook for a rule a `Field` cannot state: across fields (kyutai's `lag_drop_s` above `lag_warn_s`), on a field's content (a `ws://` URL, a two-letter language code), or on a `schema_extras` field. It raises `ConfigError` and looks at the config only.
- **`schema_extras`** gives the JSON Schema of the fields a `Field` cannot state: a list of strings (`context_bias`), a list of integers, a free-form object (`generate_kwargs`, `extra_fields`), or a field accepting a boolean or an object (`turn_detection`, described as the boolean a form draws, its description saying an object is accepted in the raw config).
- `ASRModule.sample_rates(config)` gives the rates a config block accepts and its default, so `check_config` can tell an `engine.module.audio_format` the module does not take without building it (`openai-realtime-compatible` reads its overrides there).

The registries hold the classes themselves (`REGISTRY: dict[str, type[ASRModule]]`, and the VAD's), so a module's declaration is readable on the base install. This replaced the lazy `LazyModule` entries: every module file now imports its provider only when the module is built or run ([asr-module-interface.md](asr-module-interface.md) "Module Registration").

### Keys

A key is never written in a config.

- **`api_key_env`** (`secret="env"`) is the one key field of a module that authenticates, declared by **`key_env_field(provider, variable)`**: its default is the provider's usual variable, so a fresh config works with the shell's key and a setup page lists it. `deepgram-v1` and `deepgram-v2` default to `DEEPGRAM_API_KEY`, `mistral-realtime` to `MISTRAL_API_KEY`. The two OpenAI-protocol modules have none: a compatible server often needs no key, and with no variable named no key is sent, so a key is never sent to a server the config did not name it for. Pointed at `api.openai.com` with no variable named, they are refused by `config_rules`: `"<label>: api.openai.com needs a key: name its variable in 'api_key_env', usually 'OPENAI_API_KEY'"`.
- **There is no `api_key` field.** A config holding one is refused, by `check_config` (at `/module/api_key`) and at construction, whatever its value: `"<label> does not take 'api_key': keep the key in an environment variable and name it in 'api_key_env'"`.
- **Keys the code holds are passed next to the config**: `ASREngine(config, secrets={"DEEPGRAM_API_KEY": key})`. Only a module with a key field takes `secrets` (a keyword of its constructor; `takes_secrets()`); the engine hands them only to such a module, and every other keeps `__init__(config)`.
- **`resolve_api_key(config, secrets=None)`**, a classmethod, looks the variable up in `secrets` first, then in the environment. No key under it is a **`MissingSecretError`** (a `ConfigError`) carrying `.variable` and `.label`: `"<label>: no key under 'DEEPGRAM_API_KEY': set the environment variable, or pass it in `secrets`"`.
- The provider's library or endpoint always gets the key explicitly: no SDK fallback to its own variable is relied on.

## The schema

`config_schema()["schema"]` is the engine block, `required: ["module"]`:

- **`module`**: an object titled "Module" with one `oneOf` branch per registered module, in registry order, told apart by `type`. Each branch is titled `"<title> (<kind>)"` ("Deepgram Nova (hosted)", "Kyutai (local)", "Fake (fixture)") and holds the module's `schema()`. Its common property is `audio_format` (advanced). A renamed type has no branch: the schema offers current names only, and `check_config` still accepts the old one.
- **`audio_source`**: an object titled "Input" with one branch per source type: "Input device" (`device`, with `x-options: "input-devices"`, and `audio_format`), "Audio file" (`audio_file` required, `trailing_silence_s`), "Custom source (given in code)". Optional: absent, the default input device.
- **`vad`**: an optional object titled "Voice activity detection": the gate's parameters common to every branch, one branch per registered VAD module (`silero`). A form draws it as a switch.
- **`segmentation`** and **`sound_feedback`**: advanced objects; `trigger_words` is an array of strings.
- **`auto_start`**, **`auto_start_dictation`**, **`listen_default_segmentation_mode`**, **`dictation_default_segmentation_mode`**: advanced fields.

The `fake` fixture is in the schema: it needs nothing installed and lets a person see the pipeline work before choosing a provider.

## `check_config`

It reports, in this order:

1. the engine's own problems (`engine_problems`), each at its own path;
2. an unknown module type at `/module/type`, else the module's `check_config`, each at `/module/<field>` (or `/module` for a `config_rules` rule), then an `audio_format` field the module does not take, at `/module/audio_format/<field>`;
3. an unknown VAD type at `/vad/type`, else the VAD module's `check_config`, at `/vad/<field>`;
4. a module that needs the gate without a `vad` block, or cannot take one with it, at `/vad` (the message the engine raises).

A module or VAD block the engine part already found broken is not asked about. It constructs nothing and reads no environment. A config it accepts can still be refused later for what only the machine, the provider's library or its service knows: an unset key variable, a missing extra, a model the provider does not serve, a file source that cannot be read or converted, a `custom` source with none passed. Unknown keys in a module block are accepted, as the constructors accept them; Silero refuses its own unknown keys.

## `check_setup` and `prepare`

`check_setup` is asked only about a config `check_config` accepts (the caller's duty). It returns the selected ASR module's items, then the VAD module's, each in this order:

1. **The extras**: one item per extra `required_extras` names, `extra:asr-engine[<extra>]`, titled "<title> extra", never preparable: `ready` ("Installed.") when `imports` succeeds, else `missing` with the install hint. An extra whose packages all carry another platform's marker (`mlx-audio`, `kyutai-mlx`) installs nothing there and is `missing`.
2. **The key**: one item `key:<VARIABLE>` for the variable `api_key_env` names, or its default, never preparable: `ready` when set and non-empty, else `missing` naming the variable and the field. Whether the provider accepts the key is `probe`'s business.
3. **The files**, only once the extras import: `check_files`. An exception there is one `unknown` item.

| Module | Item | How `check_files` knows | `prepare` |
|---|---|---|---|
| `kyutai` | `kyutai:<hf_repo>`, the model | `download.is_cached` (the Hugging Face cache, no network) | `download.download`, the files the backends load |
| `mlx-audio` | `mlx-audio:<model>`, the model | `download.is_cached`; a local path is `ready` or `missing`, never preparable | `download.download`, mlx-audio's own loader call |
| Silero VAD | `silero:<file>`, the pinned model | The cache file and its SHA-256; a `model_path` is `ready` or `missing`, never preparable | `ensure_model`, the call `load()` makes |

The environment read is the process's own: an application keeping keys in a file loads them first. Hosted modules and the fixture have no files.

## `options`

`"input-devices"`: the machine's input devices (`AudioCapture.list_devices()`), after the system default, each `{"label": "<name>", "fields": {"device": "<name>"}}`, the default's `{"device": null}`. It fills `audio_source.device`. Any other name returns `[]`. There is no listing of a provider's models: none of the providers offers one that matches what its realtime endpoint takes.

## `probe`

`probe(config, *, secrets=None)` checks the config, then asks each selected module (the ASR module, then the VAD) for its `probe`, and reports `{"ok", "message", "duration_s"}`; the messages of the modules are joined. It never raises: a problem, a missing extra or key, a refusal is `ok: false` with its message.

It is **a light call, not a run**: no microphone, no audio, no transcription, nothing billed.

| Module | What it does |
|---|---|
| `deepgram-v1`, `deepgram-v2` | `GET https://api.deepgram.com/v1/projects` with the key: Deepgram's model list answers without a key and omits Flux, so the key is proven on the projects and the model is left to the connection |
| `mistral-realtime` | `GET /v1/models` on the `url`'s host with the key, and the configured `model` found in the list |
| `openai-realtime-compatible`, `openai-transcriptions-compatible` | `GET <url's host and path up to /v1>/models`, with the key when `api_key_env` names one, and the `model` found in the list |
| `kyutai`, `mlx-audio` | Load the model once, then release it |
| Silero VAD | Load the model, then release it |
| `fake` | Nothing to try: it says so, at once |

A refused key reads `"<Service> refused the key (HTTP 401)."`, an unlisted model `"… does not list the model '<model>'."`. The calls use the standard library (`urllib.request`), with a 10 s timeout.

## Testing

`tests/modules/test_fields.py` (shared with the other libraries' copy): `read_fields` and `field_problems` agree for every kind and rule; `fields_schema` emits each attribute and annotation.

`tests/test_setup.py`:

- every registered module and VAD module declares `kind`, `title`, `extra` and documented fields; the extras it can require are declared in `pyproject.toml`, and only the fixture needs none;
- `config_schema()` is stable, has one branch per registered module and per VAD, and the key field carries its default variable and no `api_key` property;
- the contract's conformance check (`module_setup.contract.check_contract`) passes over the example configs' engine blocks and a file-source config;
- `required_extras` for each kind of config, kyutai by backend and by platform, and `[]` for what selects nothing;
- `check_config`: the examples pass; each engine-level problem at its path, and the loader refusing the same config with the same message; engine and module problems together; a module problem refused by the constructor with the same message, and the reverse; hosted OpenAI without a key variable; a batch module without the gate; an unsupported audio format; a renamed type;
- in a subprocess with every provider import blocked: the schema, `check_config` and `required_extras` work, and `check_setup` reports each module's extra `missing` with the hint `imports` raises, then its key, and nothing else;
- `check_setup`: each extra `ready` where it installs and `missing` with its hint elsewhere; a key variable `missing` until set (empty counts as unset); the default variables; no key item for a compatible server; the Silero model `missing`, prepared, then `ready`; a local model path never downloaded; kyutai's model prepared once;
- `options("input-devices")` and an unknown list;
- `probe`: the fixture; the Deepgram key proven on the projects list; a refused key; Mistral's model found or not; a compatible server listed without a key, hosted OpenAI with one; a config with a problem; keys given in code;
- `ASREngine(config, secrets=...)` hands them to a module that reads a key, not to the fixture.

`tests/conftest.py` sets every provider's default variable to `"k"` for each test, so a test gives a key the way a person does and the shell's real keys never reach the fast tier.

## Open questions

1. **`fields.py` is a copy.** It is shared word for word with the other libraries implementing the contract, until a common package holds it.
2. **Problems on the right field.** A `config_rules` rule is reported at `/module`, not at the field it is about (the URL scheme, the language code, `turn_detection`'s content); finer pointers need the rule to name its field.
3. **Deprecated fields and renamed types** are accepted and not offered. Whether `check_config` should report them as warnings is deferred.
4. **The demo app**, a page with a Setup tab over these functions, is [gradio-demo.md](gradio-demo.md).
