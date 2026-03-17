# Implementation Details — Plan 04: ASR Module Interface & Registry

## What was implemented

- `ASRResult` dataclass (`transcript`, `is_final`, `confidence`) in `modules/base.py`
- `ResultCallback` type alias (`Callable[[ASRResult], Awaitable[None]]`) in `modules/base.py`
- `ASRModule` ABC with abstract `start` / `stop` methods and a concrete `__init__` that stores `config`
- `REGISTRY: dict[str, type[ASRModule]] = {}` in `modules/__init__.py` (empty; Deepgram registered in plan 05)
- `load_module(asr_config)` in `modules/__init__.py`
- 13 unit tests in `tests/modules/test_base.py`

## Deviations from spec

None. The spec left `REGISTRY` initially empty (populated in plan 05), which matches the implementation.

## Non-obvious decisions

- `REGISTRY` is typed as `dict[str, type[ASRModule]]` rather than `dict[str, Any]` — this allows type checkers to validate that only `ASRModule` subclasses are registered.
- `load_module` uses `asr_config.get("type")` and checks membership in `REGISTRY` before raising, so a missing `type` key also raises `ValueError` (returns `None`, which is not in the registry).
- The `__init__` is implemented concretely on `ASRModule` itself (not abstract) so subclasses only need to override `start` and `stop`.

## Known limitations

- `REGISTRY` is a plain mutable dict — tests use `monkeypatch.setitem` to inject fakes. No thread-safety concern at import time, but concurrent registration is not safe (not a real use case).
