"""Unit tests for speech_utils.py."""

from __future__ import annotations

from asr_engine.speech_utils import contains_trigger_word


def test_exact_match_case_insensitive():
    assert contains_trigger_word("SUBMIT", ["submit"]) is True


def test_substring_match_within_sentence():
    assert contains_trigger_word("the sky is blue validate", ["validate"]) is True


def test_no_match_returns_false():
    assert contains_trigger_word("the sky is blue", ["submit", "enter"]) is False


def test_empty_word_list_returns_false():
    assert contains_trigger_word("submit this", []) is False
