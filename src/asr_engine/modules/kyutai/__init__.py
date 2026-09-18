"""Kyutai streaming STT — a local, on-device ASR module (registry key ``"kyutai"``).

- ``module.py`` — ``KyutaiModule``: every backend-independent behaviour.
- ``backend.py`` — the ``KyutaiBackend`` protocol and ``StepResult``: the seam.
- ``mlx_backend.py`` / ``torch_backend.py`` — the two implementations, each
  behind its own extra (``kyutai-mlx`` / ``kyutai-torch``).

Importing this package loads neither ``moshi_mlx`` nor ``moshi``: a backend file
is imported only once it has been selected, on the first ``start()``.
See specs/modules/kyutai.md.
"""

from asr_engine.modules.kyutai.backend import KyutaiBackend, StepResult
from asr_engine.modules.kyutai.module import KyutaiModule

__all__ = ["KyutaiBackend", "KyutaiModule", "StepResult"]
