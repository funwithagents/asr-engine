"""Mistral ASR modules (specs/modules/mistral.md).

Imports nothing: the registry resolves ``mistral_realtime`` lazily to
``asr_engine.modules.mistral.realtime``, which imports ``websockets`` (the
``mistral`` extra) at top level.
"""
