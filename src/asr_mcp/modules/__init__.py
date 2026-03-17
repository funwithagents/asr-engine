from typing import Any

# Maps asr.type string → ASRModule subclass.
# Populated by each module file; deepgram is registered in deepgram.py.
REGISTRY: dict[str, Any] = {}
