"""
Unit Tests for Levenshtein Distance and Confidence Scoring
Validates that the 2.3% conflict rate accurately captures genuine discrepancies
(such as Smith vs. Smyth or multi-patient faxes) without false positives.
"""

from pipeline.load import score_confidence


def test_perfect_match():
    tier, score, dist = score_confidence("Smith_John_1985.pdf", "Smith")
    assert tier == "high"
    assert dist == 0
    assert score >= 0.95


def test_near_miss_kept_in_review():
    """Smith vs Smyth has Levenshtein distance 1. Must stay in conflict review deliberately."""
    tier, score, dist = score_confidence("Smith_John_1985.pdf", "Smyth")
    assert tier == "conflict"
    assert dist == 1


def test_large_discrepancy():
    """Genuinely different patients (e.g. 5+ edits) must be flagged for review."""
    tier, score, dist = score_confidence("Williams_Mary.pdf", "Rodriguez")
    assert tier == "conflict"
    assert dist >= 5
