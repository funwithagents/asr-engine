# ASR module extras, lazy registry, and the `fake` module

**Status:** Done

Implements the settled behavior in `specs/asr-module-interface.md` ("Module Registration", "Optional dependencies (extras)"), `specs/modules/deepgram.md` ("Installation"), `specs/project.md` (core deps / dev environment), `specs/configuration.md` (no default module; extra-not-installed validation), `specs/modules/fake.md` (whole spec), `specs/gradio-demo.md` ("Configuration"), and the fast-tier half of `specs/testing.md` ("The `fake` module as a test double"). Delivers: Deepgram moves from a core dependency to the `deepgram` extra behind a lazy registry, so no backend is the default; a keyless scripted `FakeASRModule` (`"fake"`) plus a `fake_engine_factory` fast-tier fixture; a Gradio demo that requires `--config`; and docs that never present Deepgram — or the fake — as the default. It deliberately leaves the `tests-e2e/` default-module switch to the `fake` module to the follow-up plan [202609171831_e2e-default-module-fake.md](202609171831_e2e-default-module-fake.md).

## Scope

- `pyproject.toml` — drop `deepgram-sdk` from `dependencies`; add `[project.optional-dependencies] deepgram = ["deepgram-sdk"]`; add `"asr-engine[deepgram]"` to the `dev` dependency group.
- `src/asr_engine/modules/__init__.py` — `LazyModule` dataclass; `REGISTRY: dict[str, LazyModule | type[ASRModule]]` with `fake`, `deepgram_v1`, `deepgram_v2` as lazy entries; `resolve_module_class()`; `load_module()` goes through it. No provider import at module top level.
- `src/asr_engine/modules/fake.py` — **new**: `FakeASRModule` (config validation, audio clock, emission schedule, lifecycle).
- `src/asr_engine/engine.py` — replace the inline `REGISTRY` membership check + `REGISTRY[...]` lookup with `resolve_module_class(config.module.type)`.
- `src/asr_engine/mcp_server_cli.py` — also catch `ImportError` around `run_server` (print `Error: …`, exit 1) so a missing extra fails fast with the install hint.
- `examples/gradio_demo/app.py` — make `--config` required; delete `_default_config()` and the `ModuleConfig` import it used.
- `tests/modules/test_registry.py` — **new**: registry resolution tests.
- `tests/modules/test_fake.py` — **new**: `FakeASRModule` tests (list in the spec's "Testing").
- `tests/conftest.py` — `fake_engine_factory` fixture.
- `tests/test_engine.py` — one engine-level `fake` scenario via the fixture; migrate `_ScriptedModule`-based downstream tests to the fixture where they test segmentation/callback behavior rather than module-call contracts.
- `tests/test_mcp_server_cli.py` — use `"fake"` instead of `"deepgram_v1"` in configs whose module is irrelevant; add the missing-extra startup test.
- `README.md` — install section (`asr-engine[deepgram]`), module table (fake labeled test-only), quick start stays on Deepgram but with the extra install.
- `examples/README.md`, `examples/gradio_demo/README.md`, `examples/mcp_client/README.md`, `examples/asr_to_terminal/README.md` — install the extra; drop the "When `--config` is omitted…" paragraph from the Gradio README.
- `AGENTS.md` — "What this project is" (no "first: Deepgram" default framing), "Adding a new ASR module" (lazy `LazyModule` entry + extra), "Commands" (note the extras if the self-referencing dev group is not usable).
- Specs: add `src/asr_engine/modules/fake.py` + `tests/modules/test_fake.py` to `modules/fake.md` frontmatter and drop its "Frontmatter note"; add `pyproject.toml` + `tests/modules/test_registry.py` to `asr-module-interface.md` frontmatter; status flips (see Verification).

## Steps

1. **Packaging.** Edit `pyproject.toml` as scoped. Run `uv sync` and confirm the venv still has `deepgram` (`uv run python -c "import deepgram"`). If uv rejects the self-referencing `asr-engine[deepgram]` in the `dev` group, remove it, document `uv sync --all-extras` in AGENTS.md "Commands" and `specs/project.md`, and use that command for the rest of this plan.

2. **Lazy registry** (`modules/__init__.py`).
   - `@dataclass(frozen=True) class LazyModule: import_path: str; extra: str | None = None`.
   - `resolve_module_class(module_type)`:
     - `entry = REGISTRY.get(module_type)`; missing → `ValueError(f"Unknown ASR type '{module_type}'. Available: {', '.join(sorted(REGISTRY)) or '(none)'}")` (same message the engine raises today, so existing assertions hold).
     - `isinstance(entry, type)` → return it.
     - Else split `import_path` on `:`, `importlib.import_module(mod)`; on `ModuleNotFoundError as exc` where `exc.name` is not `asr_engine` / doesn't start with `asr_engine.`: raise `ImportError(f"ASR module '{module_type}' requires the optional dependency '{entry.extra}' which is not installed. Install it with: pip install 'asr-engine[{entry.extra}]' (from source: uv sync --extra {entry.extra})") from exc`. If `entry.extra` is `None`, re-raise unchanged (a core module missing a dependency is a packaging bug). Return `getattr(module, class_name)`.
   - `load_module(asr_config)` → `resolve_module_class(asr_config.get("type"))(config=…)` (the unknown-type check is no longer duplicated).
   - Keep `REGISTRY` a plain dict at module scope so existing `patch.dict("asr_engine.engine.REGISTRY", {...})` / `patch.dict("asr_engine.modules.REGISTRY", …)` keep working (both names bind the same object).

3. **Engine wiring** (`engine.py`): `self._module_cls = resolve_module_class(config.module.type)`; delete the inline check. Keep `from asr_engine.modules import REGISTRY` only if still referenced (tests patch the dict by that path; if the import is removed, switch those `patch.dict` targets to `asr_engine.modules.REGISTRY`).

4. **CLI** (`mcp_server_cli.py`): widen the second `except` to `(ValueError, ImportError)`.

5. **`FakeASRModule`** (`modules/fake.py`), per `specs/modules/fake.md`:
   - Class attributes: `SUPPORTED_* = None`; `DEFAULT_SAMPLE_RATE = 16000`, `DEFAULT_CHANNELS = 1`, `DEFAULT_ENCODING = "linear16"`.
   - `__init__`: validate `utterances` (each branch its own `ValueError` naming the index), then precompute an immutable schedule `list[tuple[float, SpeechUtterance]]` — for each utterance, interims `start_s + i*d/n` for `i in 1..n-1` with `" ".join(words[:i])`, then `(end_s, SpeechUtterance(text, True, confidence))`. The schedule is already time-ordered because utterances are validated non-overlapping and in order.
   - `start(audio_queue, on_utterance, on_connected=None, *, audio_format=DEFAULT_AUDIO_FORMAT)`: clear a `self._stop_event`; `byte_rate = audio_format.sample_rate * audio_format.channels * audio_format.bytes_per_sample`; `consumed = 0`; `next_idx = 0`; call `on_connected(True)`. Loop until stopped: race `audio_queue.get()` against `self._stop_event.wait()` (two tasks + `asyncio.wait(FIRST_COMPLETED)`, cancelling the loser in a `finally` so no task leaks on cancellation); on a chunk, `consumed += len(chunk)`, `clock = consumed / byte_rate`, then emit every `schedule[next_idx:]` entry with `time <= clock` in order (`await on_utterance(...)`). On exit call `on_connected(False)`.
   - `stop()`: set `self._stop_event`.
   - Module docstring and class docstring state plainly: scripted test double, ignores audio, not an ASR backend; install a provider extra for real use.

6. **Fast tests.**
   - `tests/modules/test_registry.py`: unknown type message; eager class returned as is; lazy entry imports and returns the class (`fake`); missing third-party dependency → `ImportError` whose message names the extra and the pip command (register a `LazyModule` pointing at a temp module in `tmp_path` on `sys.path` that does `import some_missing_pkg_xyz`, via `patch.dict(REGISTRY, …)`); a `ModuleNotFoundError` for a missing `asr_engine.*` path propagates unchanged; `import asr_engine` does not import `deepgram` (run in a subprocess: `python -c "import asr_engine, sys; assert 'deepgram' not in sys.modules"`).
   - `tests/modules/test_fake.py`: the scenarios listed in `specs/modules/fake.md` "Testing". Drive `start()` as a task with a hand-fed `asyncio.Queue`, putting exact byte counts to place the clock; record `(clock, transcript, is_final, confidence)` in the callback.
   - `tests/conftest.py`: `fake_engine_factory(utterances, *, on_speech_utterance=None, on_speech_segment=None, **overrides)` → `ASREngine` with `ModuleConfig(type="fake", extra={"utterances": utterances})`, `SoundFeedbackConfig(enabled=False)`, `audio_source=ScriptableAudioSource(real_time=False)`, and segmentation/default-mode overrides applied to `ASREngineConfig`.
   - `tests/test_engine.py`: add the two-utterance `trigger_word` scenario through `fake_engine_factory` (asserts the closed segment's transcript and `end_reason`). Replace `_ScriptedModule` usages whose subject is downstream behavior with the fixture; keep `_mock_module_class` / `patch.dict` where the test asserts module-call contracts (e.g. `stop()` awaited, unknown type). Don't churn tests that already pass and read clearly — migrate only where the fixture is simpler.
   - `tests/test_mcp_server_cli.py`: swap `"deepgram_v1", "api_key": "x"` configs to `{"type": "fake"}` where the module is irrelevant; add a test that a config naming a `LazyModule` whose dependency is missing (patched registry entry) makes `main()` print the install hint to stderr and exit 1.
   - Check `tests/examples/test_gradio_controller.py` and `tests/test_server.py` for `deepgram_v1` configs that only need *a* module; switch those to `fake`.

7. **Gradio demo.** `app.py`: `parser.add_argument("--config", required=True, …)`; config = `MCPServerConfig.from_json_file(args.config).engine`; remove `_default_config`. The controller needs no change (its `_ensure_engine` callers already catch `Exception` and surface the message) — confirm by selecting an uninstalled module in a quick manual run or by reading the handlers.

8. **Docs.**
   - `README.md`: Requirements bullet → "An ASR provider module installed as an extra (e.g. `asr-engine[deepgram]`) and its credentials"; install → `uv sync --extra deepgram` / `pip install 'asr-engine[deepgram]'`; module table gains an "Extra" column (`deepgram` for both Deepgram rows) and a separate note: "`fake` — scripted test double for tests (no extra, no key); not an ASR backend — see specs/modules/fake.md". No wording that implies a default backend.
   - Example READMEs: add the extra to the install step; Gradio README drops the no-`--config` fallback sentence and states `--config` is required.
   - `AGENTS.md`: "What this project is" → "Streams audio to a pluggable ASR module; real backends ship as optional extras (first: `deepgram`), and a scripted `fake` module exists for tests only." "Adding a new ASR module" step 2 → register a `LazyModule("asr_engine.modules.<name>:<Class>", extra="<extra>")` and add the extra to `pyproject.toml` (+ to the `dev` group's self-reference).

9. **Specs bookkeeping.** Update the frontmatter as scoped; flip statuses as listed in Verification.

## Verification

- `uv sync` (or `uv sync --all-extras`, per step 1), then:
- `uv run ruff check .` and `uv run ruff format --check .`
- `uv run pyright`
- `uv run pytest tests/` — all green and still a few seconds (`--durations=10` shows no `fake` test near a second), including the new `tests/modules/test_registry.py`, `tests/modules/test_fake.py`, and `tests/test_project_map.py` (frontmatter paths now exist).
- Core-install check: in a scratch venv, `uv pip install .` (no extras) → `python -c "import asr_engine"` succeeds; an `asr-engine-mcp --config` naming `deepgram_v1` exits 1 with the install hint; one naming `fake` (with `audio_file` set) starts.
- Manual: `uv run python -m examples.gradio_demo.app` without `--config` exits with an argparse usage error.
- Optional live regression for the moved imports: `zsh -ic 'uv run pytest tests-e2e -k deepgram_v1'`.

Then mark this plan `Done` (here and in [_index.md](_index.md)) and flip to `Implemented` (file + [specs/_index.md](../specs/_index.md)): `asr-module-interface.md`, `modules/deepgram.md`, `project.md`, `configuration.md`, `gradio-demo.md`, and `modules/fake.md` (Stable → Implemented). `testing.md` stays `Updated` until the follow-up e2e plan is `Done`.
