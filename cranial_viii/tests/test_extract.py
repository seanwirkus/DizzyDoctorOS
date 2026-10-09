"""
Unit Tests for NUL-Byte Stripping & Date Parser Ceiling
"""

from datetime import date
from pipeline.extract import sanitize_text, parse_clinical_date


def test_nul_byte_stripping():
    """Confirms NUL bytes (\x00) and scanner junk are stripped completely."""
    dirty = "Patient:\x00 Smith\x00, John\x00\x01\x02"
    clean = sanitize_text(dirty)
    assert "\x00" not in clean
    assert clean == "Patient: Smith, John"


def test_future_date_ceiling_rejection():
    """Mistyped future dates (2028-2035) must be rejected."""
    future_date_str = "03/15/2032"
    result = parse_clinical_date(future_date_str)
    assert result is None, "Date parser must reject dates beyond today"


def test_valid_clinical_date():
    """Valid historical date of service must parse correctly."""
    valid_str = "04/20/2021"
    parsed = parse_clinical_date(valid_str)
    assert parsed == date(2021, 4, 20)
