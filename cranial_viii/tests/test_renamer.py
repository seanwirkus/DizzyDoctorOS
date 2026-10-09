"""
Unit Tests for PaperEpley Standard Renaming Convention
"""

from datetime import date
from services.fax_slayer.renamer import generate_standard_filename, clean_name_token


def test_token_sanitizer():
    assert clean_name_token("O'Connor") == "Oconnor"
    assert clean_name_token("Smith-Jones") == "Smithjones"
    assert clean_name_token("  john  ") == "John"


def test_standard_filename_generation():
    fn = generate_standard_filename(
        last_name="Smith",
        first_name="John",
        dob=date(1982, 5, 14),
        doc_type="ProgressNote",
        dos=date(2023, 11, 2)
    )
    assert fn == "Smith_John_19820514_Progressnote_20231102.pdf"
