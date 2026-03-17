# Implementation Details — Plan 02: Configuration

## What was implemented

- `src/asr_mcp/config.py`: four dataclasses (`ServerConfig`, `AudioConfig`, `ASRConfig`, `AppConfig`), `load_config(path)`, and `validate_asr_type(config, registry)`.
- `src/asr_mcp/cli.py`: `main()` with `argparse` (`--config`, default `config.json`), calls `load_config` + `validate_asr_type`, prints a startup banner, exits with code 1 on any error.
- `src/asr_mcp/modules/__init__.py`: initialised as an empty `REGISTRY` dict ready for module registration.
- `tests/test_config.py`: 15 unit tests covering happy-path loading, all default-value combinations, and every error branch; includes CLI banner and exit-code tests.

## Deviations from spec

- `ASRConfig` stores extra module-specific fields in an `extra: dict` attribute rather than as top-level fields on the dataclass. This keeps the dataclass stable regardless of which module is active. The spec says "extra fields as dict", so this is in-spec.
- `REGISTRY` was stubbed in `modules/__init__.py` (empty dict) because Plan 02 only needs it to exist for `validate_asr_type` and `cli.py` imports. Population happens in Plan 04/05.

## Non-obvious decisions

- `load_config` re-raises `FileNotFoundError` (not `ValueError`) to give callers a chance to distinguish "file missing" from "file malformed". Both are caught in `cli.main()` and printed to stderr.
- Empty string for `asr.type` is treated the same as absent (`if not asr_type`), matching the intent that a missing or blank type is always an error.

## Known limitations

- No type validation on `server.port` (must be an integer, but we trust the JSON). Full Pydantic/strict validation was not in scope for this plan.
- `audio.device` accepts any string; existence of the device is validated later by `AudioCapture` (Plan 03).
