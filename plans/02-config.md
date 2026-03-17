# Plan 02 — Configuration

Implement config file loading, validation, and typed dataclasses.

## Tasks

- [x] Define dataclasses in `config.py`:
  - `ServerConfig` (host, port)
  - `AudioConfig` (device: `str | None`)
  - `ASRConfig` (type: str, extra fields as `dict`)
  - `AppConfig` (server, audio, asr)
- [x] Implement `load_config(path: str) -> AppConfig`:
  - Read and parse the JSON file
  - Raise `FileNotFoundError` with clear message if file is missing
  - Raise `ValueError` with clear message if JSON is invalid
  - Raise `ValueError` if `asr.type` is missing
  - Apply defaults: `server.host = "127.0.0.1"`, `server.port = 8080`, `audio.device = None`
- [x] Implement `validate_asr_type(config: AppConfig, registry: dict) -> None`:
  - Raise `ValueError` listing available types if `asr.type` is not in the registry
- [x] Implement `cli.py`:
  - Parse `--config` argument (default: `config.json`)
  - Call `load_config` and `validate_asr_type`
  - Print a clear startup banner with resolved host/port and ASR type
- [x] Manual test: run server with missing file, bad JSON, unknown asr type — verify error messages
