# CI on GitHub Actions

**Status:** In progress

Implements [specs/ci.md](../specs/ci.md) in full, the "Platforms" decision of [specs/project.md](../specs/project.md), and the UTF-8 config read of [specs/configuration.md](../specs/configuration.md) ("Constructors"). It delivers the workflow — the static gate on Linux, the fast tier on Linux, macOS and Windows, the live tier on Linux — and fixes what the first runs on each platform surface. It leaves out a Kyutai live run in CI (local opt-in, [ci.md](../specs/ci.md) open question 1), setting the `DEEPGRAM_API_KEY` repository secret and requiring the status checks on `main` (repository settings, the owner's step).

## Scope

- `.github/workflows/ci.yml` (new) — `check`, `fast-tier` (three-OS matrix), `e2e`
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
- On GitHub: `check`, `fast-tier (ubuntu-24.04)`, `fast-tier (macos-15)`, `fast-tier (windows-2025)` and `live tier` green on the pull request; the live job's skips exactly the expected table.
- Owner's steps, outside the repo: add the `DEEPGRAM_API_KEY` repository secret; require the five checks on `main`.

## Shakeout

What the runners found that the Mac never had — one line per change: the symptom, the fix, the spec it touches.

- **Windows: the config file read with the locale encoding** (found by inspection before the first run). `open(path)` decodes with cp1252 on Windows, garbling non-ASCII trigger words. Fix: `encoding="utf-8"`. Spec: `configuration.md` ("Constructors"), `project.md` ("Platforms").
