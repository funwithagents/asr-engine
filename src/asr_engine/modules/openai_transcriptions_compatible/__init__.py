"""OpenAI transcriptions-compatible ASR module
(specs/modules/openai-transcriptions-compatible.md).

Imports nothing: the registry resolves ``openai_transcriptions_compatible`` lazily
to ``asr_engine.modules.openai_transcriptions_compatible.module``, which imports
``httpx`` (the ``openai-transcriptions-compatible`` extra) at top level.
"""
