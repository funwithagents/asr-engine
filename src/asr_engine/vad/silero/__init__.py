"""Silero VAD on ``onnxruntime`` (VAD type ``silero``). See specs/vad.md.

Empty on purpose: importing the package must not import ``onnxruntime``. The
registry imports ``asr_engine.vad.silero.module`` when the type is selected.
"""
