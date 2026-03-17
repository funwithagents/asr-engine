# Plan 04 — ASR Module Interface & Registry

Implement the abstract base class, result dataclass, and module registry.

## Tasks

- [ ] Implement `ASRResult` dataclass in `modules/base.py`:
  - Fields: `transcript: str`, `is_final: bool`, `confidence: float | None`
- [ ] Implement `ResultCallback` type alias: `Callable[[ASRResult], Awaitable[None]]`
- [ ] Implement `ASRModule` ABC in `modules/base.py`:
  - Abstract method `start(audio_queue: asyncio.Queue[bytes], on_result: ResultCallback) -> None`
  - Abstract method `stop() -> None`
  - Constructor accepts `config: dict` and stores it
- [ ] Populate `REGISTRY` dict in `modules/__init__.py`:
  - Initially empty `{}` (Deepgram added in plan 05)
- [ ] Implement `load_module(asr_config: dict) -> ASRModule` in `modules/__init__.py`:
  - Reads `asr_config["type"]`, looks up in `REGISTRY`
  - Instantiates the module with the config dict (minus the `type` key)
  - Raises `ValueError` for unknown type
