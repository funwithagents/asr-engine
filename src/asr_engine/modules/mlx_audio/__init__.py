"""mlx-audio ASR module (specs/modules/mlx-audio.md).

Imports nothing: the registry resolves ``mlx_audio`` lazily to
``asr_engine.modules.mlx_audio.module``, which imports no third-party package;
only ``runner.py`` imports mlx-audio, when the model loads.
"""
