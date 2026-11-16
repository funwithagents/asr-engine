# Documentation restructure: landing-page README, `docs/` by task, drift guards

**Status:** Done

Implements the documentation layers and checks settled in `specs/project.md` ("Documentation", "Documentation checks") and step 7 of the module checklist in `specs/asr-module-interface.md`. It delivers a short README, a task-oriented `docs/` tree with one page per backend, a `CONTRIBUTING.md`, and two fast-tier test files that keep them in step with the code. It changes no library code and no behavior: `src/` is untouched.

The shape follows the sibling projects `tts-engine` and `reachy-mini-bridge`, adapted: flat guide pages (as in `tts-engine`) since the API is small, and no benchmark or audition section, which have no ASR equivalent yet.

## Why

- The README had grown to 774 lines and carried everything, including about 200 lines of per-module field tables.
- Nothing sat between the README and the specs, which are written as design contracts, not as user guides.
- Nothing guarded the docs against drift, and they had drifted: after the hyphenated-module-names change, the README, three example READMEs and four specs still named `examples/config/deepgram_v2.json` and its siblings, so the documented `cp` command failed.

## Scope

- `README.md` — rewritten as a landing page (about 190 lines): what it is, install, a keyless quick start on the `fake` module followed by the two-line change to real speech (hosted or local), how the engine works, the module table, documentation links, examples, feedback, how it is developed, license. The clone URL replaces the `<repo-url>` placeholder.
- `docs/index.md` — the documentation by task ("I want to…").
- `docs/configuration.md`, `docs/running-the-engine.md`, `docs/segmentation.md`, `docs/audio-input.md`, `docs/vad.md`, `docs/agents-and-mcp.md` — the README's former sections, one page each.
- `docs/choosing-a-module.md`, `docs/testing-your-application.md`, `docs/troubleshooting.md` — new pages, written from the specs and the library's own error messages.
- `docs/modules/index.md` and `docs/modules/{deepgram,mistral,openai-realtime-compatible,openai-transcriptions-compatible,kyutai,mlx-audio,fake}.md` — the comparison table and every extra, then one page per backend mirroring `specs/modules/`, each with its install line, a minimal config, the full field table, behavior notes, troubleshooting and links.
- `CONTRIBUTING.md` — how the project is run: one maintainer, no external pull requests for now, feedback through issues.
- `examples/README.md`, `examples/gradio_demo/README.md`, `examples/mcp_client/README.md`, `specs/configuration.md`, `specs/modules/{mistral,mlx-audio,openai-realtime-compatible,openai-transcriptions-compatible}.md` — stale underscore `examples/config/` paths corrected to the hyphenated file names.
- `tests/test_docs.py` — relative links and anchors resolve; every `examples/config/*.json` named in the text exists; the two module tables list exactly the registered types, link each to an existing page and name the registry's extra; each `docs/modules/` page lists the fields of its spec.
- `tests/test_docs_examples.py` — runs the Python examples of the README and `docs/` with `fake` standing in for the provider and a fast silence source for the microphone.
- `specs/project.md` — "Documentation" and "Documentation checks" added, the repo shape and the frontmatter `tests:` list updated. The spec went `Implemented` → `Updated` → `Implemented` within this change.
- `specs/asr-module-interface.md` — checklist step 7: the docs page, the two table rows, the example config.
- `specs/_index.md`, `plans/_index.md`, `AGENTS.md` — the index rows, the project-map rows for `README.md`, `CONTRIBUTING.md` and `docs/`, and the "Keep the documentation current" rule.

## Steps

- [x] 1. Fix the stale `examples/config/` paths in the example READMEs and the specs.
- [x] 2. Write `docs/`: move each README section to its page, then write the three new guides and the module pages (fields taken from each module's spec table, including the ones the README left out, such as `url`, `final_margin_ms`, `idle_close_s` and the batch fields).
- [x] 3. Rewrite the README around a keyless quick start: the `fake` module with a `ScriptableAudioSource`, waiting on the final segment, so the same block runs as written for a reader and instantly under test.
- [x] 4. Add `CONTRIBUTING.md`.
- [x] 5. Add `tests/test_docs.py` and `tests/test_docs_examples.py`. Structure each tested example as the first `python` block under its heading.
- [x] 6. Update `specs/project.md`, the module checklist, both indexes and `AGENTS.md`.

## Decisions taken while building

- **Module pages mirror specs, not registry keys.** `deepgram-v1` and `deepgram-v2` share `docs/modules/deepgram.md`, as they share a spec, so the fields check compares one page with one spec. The module tables still have one row per registered type.
- **No `kind` attribute on modules.** `tts-engine` checks a `kind` column against a class attribute; here the "Kind" column (hosted, local, test double) is prose and the tables are checked on what the registry does know: the type and its extra. Adding the attribute would be a library change, out of scope.
- **Config paths named in code are checked too.** The link checker skips code on purpose, but the bug that prompted this plan lived in a `cp` command, so a separate check covers `examples/config/*.json` wherever it is written.
- **Not done:** the `docs/vad.md` field table is not checked against `specs/vad.md`, whose table has an "other fields" row; and the per-module "Audio and behaviour" prose is not machine-checked.

## Verification

- `uv run pytest tests/test_docs.py tests/test_docs_examples.py` — the new guards.
- The README quick start run as written, in real time, outside pytest.
- The gate: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest tests/`.
- CI on the pull request: the fast tier on Linux, macOS and Windows runs the same guards.
