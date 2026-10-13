# CI on GitHub Actions

**Status:** In progress

Implements [specs/ci.md](../specs/ci.md) in full, the "Platforms" decision of [specs/project.md](../specs/project.md), and the UTF-8 config read of [specs/configuration.md](../specs/configuration.md) ("Constructors"). It delivers the workflow — the static gate on Linux, the fast tier on Linux, macOS and Windows, the live tier on Linux — and fixes what the first runs on each platform surface. It leaves out a Kyutai live run in CI (local opt-in, [ci.md](../specs/ci.md) open question 1), setting the `DEEPGRAM_API_KEY` repository secret and requiring the status checks on `main` (repository settings, the owner's step).

## Scope

- `.github/workflows/ci.yml` (new) — `check`, `fast-tier` (four-entry matrix: Linux x86_64 and arm64, macOS, Windows), `e2e`
- `src/asr_engine/config.py` — `_read_file` opens the config file as UTF-8 explicitly (the locale default is cp1252 on Windows)
- `tests/test_config.py` — a UTF-8 config with non-ASCII trigger words loads intact
- `specs/ci.md` (new), `specs/project.md` ("Platforms", repo shape), `specs/configuration.md`, `specs/testing.md` (open question resolved), `specs/e2e-testing.md` (non-goal note), `specs/_index.md`
- `AGENTS.md` — `.github/workflows/` in the top-level layout; CI under "Verification"
- `README.md` — the supported platforms and Linux's PortAudio under "Requirements"
- `plans/_index.md`, this file — status

## Steps

1. Write `specs/ci.md` (`Stable`) and the "Platforms" section; mark `project.md` and `configuration.md` `Updated`; index rows.
2. The UTF-8 fix in `config.py` and its test.
3. The workflow file, mirroring the reachy-mini-bridge layout: `on: pull_request / push: main / workflow_dispatch`, one run per ref, pinned images, `astral-sh/setup-uv` with the cache keyed on `uv.lock`, `uv sync --locked` in every job, `timeout-minutes` on every job; `libportaudio2` from apt for the live job only; `DEEPGRAM_API_KEY` mapped from the secret on the live step.
4. Push the branch, open a pull request, and read every job. Fix what Linux and Windows surface; record each fix under "Shakeout" below with the spec it touches. Check the live job's `-rs` summary against the expected-skips table of `ci.md`.
5. Once all five jobs are green: `ci.md`, `project.md`, `configuration.md` → `Implemented`; this plan `Done`; index rows in sync.

## Verification

- Locally: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest`; `uv run pytest tests-e2e -rs` green with only the expected skips.
- On GitHub: `check`, `fast-tier (ubuntu-24.04)`, `fast-tier (ubuntu-24.04-arm)`, `fast-tier (macos-15)`, `fast-tier (windows-2025)` and `live tier` green on the pull request; the live job's skips exactly the expected table.
- Owner's steps, outside the repo: add the `DEEPGRAM_API_KEY` repository secret; require the six checks on `main`.

## Shakeout

What the runners found that the Mac never had — one line per change: the symptom, the fix, the spec it touches.

- **Windows: the config file read with the locale encoding** (found by inspection before the first run). `open(path)` decodes with cp1252 on Windows, garbling non-ASCII trigger words. Fix: `encoding="utf-8"`. Spec: `configuration.md` ("Constructors"), `project.md` ("Platforms").
- **Linux fast tier: `test_no_mcp_import` failed with `PortAudio library not found`.** Its subprocess imports `asr_engine` for real, outside `tests/conftest.py`'s `sounddevice` stub, and the Linux runner has no system PortAudio. Fix: the subprocess stubs `sounddevice` itself, as `tests/modules/test_registry.py`'s subprocess already did — the fast tier stays free of system packages on every OS. No spec change (`ci.md` already says the tier stubs `sounddevice`).
- **Linux and Windows fast tier: `test_idle_session_recycles_before_the_budget_runs_out` saw `30 < 30`.** The test fed 40 frames in one burst; the module's idle-recycle request travels on the frame queue, so it landed behind every queued frame and the budget ran out first (the module's exhaustion fallback then recycled at 30). Faster than real time is not the scenario the test names: fed one frame at a time, as a real-time source delivers them, the recycle lands inside the margin on every platform. Test-only fix; no spec change — under a real backlog the exhaustion fallback is the designed path ([modules/kyutai.md](../specs/modules/kyutai.md)).
- **What needed no fix on the first run:** the static gate, `uv sync --locked` on all three OSes, the macOS fast tier, and the live tier (11 passed; skips exactly the expected table — the two Deepgram cases on the unset secret, `kyutai` on the opt-in).
- **Added after the first green runs: `ubuntu-24.04-arm` in the fast-tier matrix.** Reviewing architectures: the code has no arch-specific path, so an entry proves the lock's wheels; a wheels-only resolve of the locked set succeeded for arm64 Linux, x86_64 macOS and Windows. arm64 Linux is a realistic host (Raspberry Pi, Jetson) and gets an entry; Intel macOS adds no code path the other entries miss and is declared best effort instead. Spec: `ci.md` ("The runners", the job table, required checks), `project.md` ("Platforms").

## Measurements

Run `38399535112`, the first all-green run (cold caches): `check` 17 s, `fast-tier` 21 s (Linux), 21 s (macOS), 31 s (Windows), `live tier` 62 s (pytest 41 s: 11 passed, 3 skipped — the expected table). The run end to end: about a minute.
