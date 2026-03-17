"""Shared fixtures and test-environment setup."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Stub out sounddevice before any test module imports it so that the PortAudio
# shared library is never needed in the test environment.
_sd_mock = MagicMock()
sys.modules.setdefault("sounddevice", _sd_mock)
