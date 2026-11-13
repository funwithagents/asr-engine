"""OpenAI Realtime-compatible ASR module (specs/modules/openai-realtime-compatible.md).

Imports nothing: the registry resolves ``openai_realtime_compatible`` lazily to
``asr_engine.modules.openai_realtime_compatible.module``, which imports
``websockets`` (the ``openai-realtime-compatible`` extra) at top level.
"""
