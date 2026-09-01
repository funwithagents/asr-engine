# E2E per-provider restructure

**Status:** Done

Implements the settled behavior in `specs/e2e-testing.md` (restructured "Test Cases" +
naming). Reworks `tests-e2e/` so that the **module contract** is verified per provider
(parametrized over the module registry) while the **module-agnostic stack** (MCP
resource, MCP tools, asr-to-terminal) is verified once, on a single provider. Also
drops the redundant `e2e` from test names (the folder already says it).

## Rationale (the split)

- **Per provider** = only what a real backend does differently: emit interim then
  final utterances with the right transcript, and finalize on silence. Observed
  through `ASREngine`. Adding a module = adding one row to a param table.
- **Single provider** = everything downstream of `SpeechUtterance`/`SpeechSegment`.
  The `Segmenter`'s three modes (incl. interim/open segments, trigger-word exclusion,
  both timeout paths) are already covered deterministically in the fast tier
  (`tests/test_segmenter.py`), so the e2e layer must not re-run that matrix per
  provider — it would only re-test module-agnostic code over the network.

## Scope

Files this plan touches:

- `tests-e2e/test_engine_e2e.py` → **rename** to `tests-e2e/test_engine_modules.py` —
  becomes the parametrized-per-provider conformance file (3 tests × N providers),
  driving each module through the engine ("engine × modules").
- `tests-e2e/test_asr_resource_client.py` → **rename** to `tests-e2e/test_mcp_resource.py` —
  drop the duplicated `deepgram_v2` case; keep a single-provider resource test.
- `tests-e2e/test_mcp_tool_client.py` → **rename** to `tests-e2e/test_mcp_tools.py` —
  single provider; drop `e2e` from function names.
- `tests-e2e/test_asr_to_terminal.py` — keep single provider; drop `e2e` from
  function names only.
- `tests-e2e/helpers.py` — (a) replace `load_api_key()` (reads the literal key) with
  `require_api_key(module_config)` (config-driven: skips when the env var *that config
  names* is unset, returns nothing); (b) add `default_provider()` — the single, central
  provider choice for the module-agnostic stack; (c) add the `MODULES` param table for
  the parametrized engine tests. Both carry `api_key_env` (the env var *name*) instead
  of a literal key — our own `resolve_api_key` resolves it. The literal
  `"DEEPGRAM_API_KEY"` lives only in the Deepgram-specific `DEEPGRAM_API_KEY_ENV`
  constant; nothing generic is tied to a provider.
- `specs/e2e-testing.md` — update `tests:` frontmatter to the new filenames; rewrite
  "Test Cases" for the per-provider/single-provider split; note the no-`e2e` naming
  convention. Set status `Implemented` → `Updated` while implementing, back to
  `Implemented` once verified.
- `specs/_index.md` — reflect the e2e-testing status transition.
- `plans/_index.md` — add this plan's row (done now, at Todo).
- `AGENTS.md` — (a) add a step to "Adding a new ASR module": add a `MODULES` row in
  `tests-e2e/helpers.py` (module type, model, silence timing) so the new backend gets
  e2e conformance coverage; (b) in "Live/e2e tests", document the per-provider vs
  single-provider split and how to run one module (see below).

No `src/` changes: `ScriptableAudioSource`, `FileAudioSource`, and the registry all
already exist.

## API key handling (no literal key, provider-agnostic)

Tests never read the key value. Each module config carries `api_key_env` — the *name*
of the env var it authenticates with — and `resolve_api_key` (in `modules/base.py`)
resolves it. The skip guard is **config-driven**, not tied to any provider: it reads
the env var *the config names*, so other modules can use a different var and a keyless
module (no `api_key_env`) is never skipped. To keep e2e **opt-in**, it skips when that
var is unset — otherwise `resolve_api_key` would *raise* and fail the test.

```python
# Deepgram-specific: other modules name their own var in their MODULES row; a keyless
# module names none.
DEEPGRAM_API_KEY_ENV = "DEEPGRAM_API_KEY"


def require_api_key(module_config: dict) -> None:
    """Skip (e2e is opt-in) unless the env var this config names is set. Returns no
    key — config carries api_key_env and our own resolve_api_key reads the var."""
    env_name = module_config.get("api_key_env")
    if env_name and not os.environ.get(env_name):
        pytest.skip(f"e2e: '{env_name}' not set — see AGENTS.md 'Live/e2e tests'")
```

## Central single-provider config (module-agnostic stack)

One helper is the single place the provider is chosen for every module-agnostic test,
so none of them hardcodes `deepgram_v1`/`nova-3`:

```python
def default_provider() -> tuple[str, dict]:
    """(module_type, module_config) for the single-provider e2e tests. Skips if the
    module's key env var is unset; carries api_key_env, not a literal key."""
    module_type = "deepgram_v1"
    module_config = {"model": "nova-3", "api_key_env": DEEPGRAM_API_KEY_ENV}
    require_api_key(module_config)
    return module_type, module_config
```

## The per-provider param table

For the parametrized engine tests, a table keyed by module carrying model, per-provider
finalization timing (Flux needs a longer silence than nova-3), and each module's own
`api_key_env` (omitted for a keyless module). Each conformance test calls
`require_api_key(module_config)` with its parametrized config.

```python
# (module_type, module_config, silence_s) — silence_s tunes the gap the backend
# needs to finalize an utterance (Flux/EndOfTurn > nova-3/is_final).
MODULES = [
    pytest.param(
        "deepgram_v1",
        {"model": "nova-3", "api_key_env": DEEPGRAM_API_KEY_ENV},
        3.0,  # must exceed the listen-timeout test's 2.0s end-of-speech timeout
        id="deepgram_v1",
    ),
    pytest.param(
        "deepgram_v2",
        {"model": "flux-general-en", "api_key_env": DEEPGRAM_API_KEY_ENV},
        6.0,
        id="deepgram_v2",
    ),
]
```

`@pytest.mark.parametrize("module_type, module_config, silence_s", MODULES)` on each
conformance test, with `require_api_key(module_config)` called at the top.

## The three per-provider tests (`test_engine_modules.py`)

1. **`test_engine_streams`** — `ScriptableAudioSource`, `segmentation_mode="utterance"`.
   Play `sample.wav`, wait for its final segment, then play `sample_submit.wav`, wait
   for a second final segment. Record **all** utterances and **all** segments (do not
   filter on `is_final`, so interim/open ones are captured). Assert:
   - `any(not u.is_final for u in utterances)` — interim utterances flowed.
   - a final utterance whose normalized transcript contains `"the sky is blue"`.
   - `any(not s.is_final for s in segments)` — an interim/open segment was observed.
   - `>= 2` final segments, each `end_reason == "utterance"`; the second contains
     `"validate"` — proving finalize-on-silence resets between utterances.
   - This single test covers all four observables the design calls for (interim/final
     utterance, interim/final segment).

2. **`test_listen_trigger_word`** — `build_file_engine(sample_submit.wav, ...,
   trigger_words=["validate"])`, `engine.listen(mode="trigger_word")`. Assert
   `end_reason == "trigger_word"`, `"validate"` not in the transcript, engine stopped.

3. **`test_listen_timeout`** — `build_file_engine(sample.wav, ...,
   end_of_speech_timeout_s=2.0)`, `engine.listen(mode="timeout")`. Assert
   `end_reason == "end_of_speech_timeout"`, transcript == `"the sky is blue"`.

`listen` tests use `FileAudioSource` (play-on-start is exactly right for a one-shot
listen); `ScriptableAudioSource` is reserved for the multi-utterance streaming test
where mid-utterance timing must be controlled.

## Single-provider stack (via `default_provider()`)

Behavior unchanged from today. Each test gets its provider from `default_provider()`
(no hardcoded module) and is **named for the behavior it tests, not the module** —
the module isn't the point here, the function under test is:

- `test_mcp_resource.py`: one test — `test_resource_emits_final_transcript` (the
  resource path yields a final transcript equal to `"the sky is blue"`). The old
  module-named `test_e2e_deepgram_v1`/`test_e2e_deepgram_v2` pair is gone: v2 moves to
  the conformance file, and the surviving test is named for the behavior.
- `test_mcp_tools.py`: `test_listen_trigger_word`, `test_listen_streaming`,
  `test_listen_timeout` — already behavior-named; just drop `e2e`.
- `test_asr_to_terminal.py`: `test_terminal_typing`, `test_terminal_submit`,
  `test_asr_to_terminal_timeout` — already behavior-named; just drop `e2e`.

The parametrized engine tests keep behavior-based function names too
(`test_engine_streams`, `test_listen_trigger_word`, `test_listen_timeout`); the module
identity shows up only as the pytest param id (`[deepgram_v1]`/`[deepgram_v2]`), which
is exactly where it belongs.

## Running a single module

The per-provider tests carry a pytest param id equal to the module name, so `-k`
selects one backend with no extra machinery:

```bash
zsh -ic 'uv run pytest tests-e2e -k deepgram_v1'   # conformance for one module only
zsh -ic 'uv run pytest tests-e2e'                  # every module + the single-provider stack
```

Because the single-provider stack is behavior-named (no module in the name), the first
command runs *only* that module's conformance tests. This gets documented in AGENTS.md.

## Steps

1. In `helpers.py`: replace `load_api_key()` with `require_api_key(module_config)`, add
   `default_provider()`, and add the `MODULES` param table — all using `api_key_env`.
2. Create `test_engine_modules.py` with the three parametrized tests; port the
   `ScriptableAudioSource` scenario from the current `test_engine_e2e.py` and grow its
   assertions (interim segment; ≥2 final segments). Delete `test_engine_e2e.py`.
3. Rename `test_asr_resource_client.py` → `test_mcp_resource.py`; collapse to one
   behavior-named test (`test_resource_emits_final_transcript`) built from
   `default_provider()`.
4. Rename `test_mcp_tool_client.py` → `test_mcp_tools.py`; drop `e2e` from names;
   source the provider from `default_provider()`.
5. Drop `e2e` from `test_asr_to_terminal.py` function names; source the provider from
   `default_provider()`.
6. Update `specs/e2e-testing.md` (frontmatter `tests:`, "Test Cases", naming note) and
   its status; sync `specs/_index.md`.
7. Update `AGENTS.md`: add the `MODULES`-row step to "Adding a new ASR module", and
   document the per-provider/single-provider split + the `-k <module>` one-module run
   in "Live/e2e tests".
8. Flip this plan and the spec back to their done states once verified.

## Verification

- `zsh -ic 'uv run pytest tests-e2e'` — all conformance tests pass for **both**
  providers; single-provider tests pass. (Live tier; needs `DEEPGRAM_API_KEY`.)
- `uv run pytest tests/` — fast tier still green (incl. `test_project_map.py`, which
  enforces spec frontmatter honesty).
- `uv run ruff check .` and `uv run pyright` clean.
- Mark this plan `Done` (here and in [_index.md](_index.md)) and `specs/e2e-testing.md`
  back to `Implemented` only once all pass.
