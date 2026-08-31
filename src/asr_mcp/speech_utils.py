"""Shared speech recognition utilities."""

from __future__ import annotations


def contains_trigger_word(transcript: str, words: list[str]) -> bool:
    """Return True if any word in *words* appears in *transcript* (case-insensitive substring match)."""
    if not words:
        return False
    lower = transcript.lower()
    return any(word.lower() in lower for word in words)
