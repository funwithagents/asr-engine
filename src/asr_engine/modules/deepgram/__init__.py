"""Deepgram ASR modules (registry keys ``"deepgram-v1"`` / ``"deepgram-v2"``).

``v1.py`` is Listen v1 (nova-*), ``v2.py`` is Listen v2 (Flux). Both need the
``deepgram`` extra. This file deliberately imports neither: the registry names
each module file directly, so selecting one never imports the other.
See specs/modules/deepgram.md.
"""
