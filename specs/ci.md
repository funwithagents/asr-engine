---
code:
  - .github/workflows/ci.yml
  - pyproject.toml
tests:
---

# Continuous integration

**Status:** Implemented

## Purpose

Both test tiers run on GitHub's hosted runners for every pull request and every push to `main`, so the verification gate of [AGENTS.md](../AGENTS.md) — lint, format, type check, tests — is a machine's verdict on each change and not only a local command. CI runs what the tiers are designed to run without hardware ([testing.md](testing.md)): the fast tier whole, on every supported platform, and the live tier on Linux, its skips read as part of the verdict. Like [testing.md](testing.md) this is a cross-cutting practice, not a runtime concept: nothing here ships in the library, and the one file that implements it is the workflow.

## Decided

### The runners

- **GitHub-hosted runners, the free tier.** The repo is public, so every runner — Linux, macOS, Windows — costs nothing. No self-hosted runner. Every image is pinned by name (`ubuntu-24.04`, `ubuntu-24.04-arm`, `macos-15`, `windows-2025`), never `*-latest`, so the system packages the jobs were validated against move only when a pin is bumped on purpose.
- **One Python**, the floor of `requires-python` that `.python-version` names (3.11); `uv` installs it on the runner.
- **The fast tier on every supported platform** ([project.md](project.md) "Platforms"). It needs no network, no credentials and no sound device — `tests/conftest.py` stubs `sounddevice`, so PortAudio is never loaded — so it runs unchanged on the three runners, and each is where that platform's own paths meet a machine of the platform: file encodings, path separators, the asyncio event loop, the lock's environment markers (`kyutai-mlx` resolving to nothing off Apple Silicon). `macos-15` is arm64, so the MLX extra installs there and its fast tests run on it.
- **Two architectures on Linux.** The code has no architecture-specific path beyond the kyutai backend choice (unit-tested by monkeypatching the platform), so what an architecture entry proves is the lock: that every locked package installs from a prebuilt wheel there. `ubuntu-24.04-arm` covers the arm64 Linux boards an on-device engine targets (Raspberry Pi, Jetson), where audio libraries and the demo's compiled dependencies have historically lagged on wheels. **Intel macOS has no entry:** it adds no code path the x86_64 Linux/Windows entries and the arm64 macOS entry do not already exercise (off Apple Silicon `kyutai-mlx` resolves to nothing, as on Linux and Windows), and GitHub's Intel macOS image is the last of its line — it is best effort ([project.md](project.md) "Platforms"). Development happens on macOS: **CI is the project's Linux and Windows target**, and a failure on either is a bug to fix, not a reason to drop the platform.
- **The static gate on Linux only.** ruff is platform-independent; pyright resolves platform markers but the code carries no platform-gated typing beyond the kyutai backends' file-level suppressions ([modules/kyutai.md](modules/kyutai.md) "Installation"), so one platform suffices.
- **The live tier on Linux only.** Its scenarios are platform-independent: the keyless ones drive the `fake` module through a subprocess MCP server and real-time file audio, and the per-module conformance cases talk to a provider over the network. A second platform would re-run the same network calls. The only module whose live run depends on the host is `kyutai` (MLX needs Apple Silicon), and it stays a local opt-in run: it needs ~2 GB of weights fetched ahead of time ([e2e-testing.md](e2e-testing.md) "Local models"), and a shared virtualized macOS runner gives no reliable real-time budget for an on-device model.
- **System packages** are apt-installed on the Linux live job only: `libportaudio2`. The fast tier stubs `sounddevice`, but the live tier imports `asr_engine.audio` for real, and on Linux `sounddevice` loads the system PortAudio (its macOS and Windows wheels bundle it). `soundfile`'s wheels bundle libsndfile on every platform, so file audio needs nothing else.

### The workflow

One file, `.github/workflows/ci.yml`. It triggers on `pull_request`, on `push` to `main`, and on `workflow_dispatch`. Concurrency is one run per ref: a newer push cancels the older run still in flight.

| Job | Runner | What |
|---|---|---|
| `check` | `ubuntu-24.04` | `uv sync --locked`, then `ruff check .`, `ruff format --check .`, `pyright` — the static gate |
| `fast-tier` | `ubuntu-24.04`, `ubuntu-24.04-arm`, `macos-15`, `windows-2025` | `uv sync --locked`, then `pytest` — a four-entry matrix, `fail-fast: false` so one platform's failure leaves the others running |
| `e2e` | `ubuntu-24.04` | `libportaudio2` from apt, `uv sync --locked`, then `pytest tests-e2e -rs` with `DEEPGRAM_API_KEY` mapped from the repository secret |

The six jobs run side by side and none waits on another: a run takes as long as its slowest job (the live tier, about a minute of real-time audio). Gating the tiers behind the static gate would only save runner time on a commit that fails lint — free on a public repo — and would add the gate's time to every green run.

- **`--locked`.** The sync fails when `uv.lock` does not match `pyproject.toml`, so a dependency edit lands with its relock or not at all.
- **The default groups install** (`dev`, `demo`): the same environment as a contributor's plain `uv sync`, so pyright sees `examples/` and its Gradio import, and both tiers see every provider module and the MCP server. `kyutai-torch` stays out, as it does locally ([project.md](project.md)).
- **`uv` is set up by `astral-sh/setup-uv`** with its cache keyed on `uv.lock`.
- **Every job carries a `timeout-minutes`** well under GitHub's default, so a hung server subprocess fails the job in minutes rather than hours.

### Secrets, and the live tier's expected skips

- **No secret is required.** Every job is green with none: the per-module conformance cases skip on the unset key ([e2e-testing.md](e2e-testing.md)). With the `DEEPGRAM_API_KEY` repository secret set, the two Deepgram cases run on every pull request and push to `main` — a few seconds of audio each. A fork's pull request never receives a secret, so they skip there.
- **Expected skips.** A skip is not a pass. The live job runs with `-rs`, which prints every skip and its reason into the log; on the runner these skip by design, and any other skip is an environment regression to investigate:

| Skips on the runner | Why |
|---|---|
| `kyutai` conformance case | local model, opt-in only (`ASR_ENGINE_E2E_KYUTAI`), never set in CI |
| `deepgram_v1`, `deepgram_v2` conformance cases | only when the repository holds no `DEEPGRAM_API_KEY` secret, or on a fork's pull request |

- **The status checks to require on `main`** are `check`, the four `fast-tier` entries and `e2e`. Requiring them is a repository setting on GitHub, outside the repo.

## Relationship to the other specs

- **[testing.md](testing.md):** the two tiers and the functional-test rules CI runs; CI adds no rule about what a test does.
- **[e2e-testing.md](e2e-testing.md):** the live tier's two skip axes (credentials by name, local-model opt-in) that make it safe to run on a runner with no key and no weights.
- **[project.md](project.md):** the supported platforms the fast-tier matrix mirrors, the dependency groups CI installs, `.github/workflows/` in the repo shape.

## Open questions

1. **A Kyutai live run in CI.** A scheduled or manually dispatched `macos-15` job, caching the Hugging Face weights, could cover the MLX path end to end. Deferred until the local opt-in run proves insufficient — and until a runner's real-time margin for the model is measured.
2. **More than one Python.** The matrix pins the floor (3.11). Adding the newest supported Python is a one-word change, deferred until a version-specific break shows up.
