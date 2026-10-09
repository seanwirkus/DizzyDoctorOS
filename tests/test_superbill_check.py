"""Batch checker tests. Patient examples are synthetic."""
import os
import tempfile
import unittest
from datetime import date

from recordfinder import superbill_check as check


class MarkDetectionTests(unittest.TestCase):
    def test_print_residue_on_both_sides_is_not_a_circle(self):
        import numpy as np

        ink = np.zeros((120, 120), dtype=bool)
        rect = (50, 50, 70, 60)
        ink[55, 43:49] = True
        ink[55, 71:77] = True
        self.assertFalse(check.ringed(ink, rect, dpi=72))
        ink[56, 43:50] = True
        ink[56, 70:77] = True
        self.assertTrue(check.ringed(ink, rect, dpi=72))

    def test_single_attending_x_row_wins_over_a_wide_provider_circle(self):
        reading = check.Reading(providers=["PATEL", "YARI"], attending=[("left", "YARI")])
        self.assertEqual(check._marked_provider_rows(reading), ["YARI"])
        reading.attending = [("left", "YARI"), ("right", "PATEL")]
        self.assertEqual(check._marked_provider_rows(reading), ["PATEL", "YARI"])


class BatchInputTests(unittest.TestCase):
    def test_batch_files_deduplicates_and_skips_its_own_review(self):
        with tempfile.TemporaryDirectory() as folder:
            batch = os.path.join(folder, "AB 10.06.26.pdf")
            review = os.path.join(folder, "Superbill review 2026-10-06.pdf")
            manual = os.path.join(folder, "Superbill manual review 2026-10-06.pdf")
            other = os.path.join(folder, "notes.txt")
            for path in (batch, review, manual, other):
                open(path, "w").close()
            self.assertEqual(check.batch_files([folder, batch, review, manual]), [os.path.abspath(batch)])

    def test_manual_worklist_does_not_export_machine_code_findings(self):
        packet = check.superbills.Packet(0, 1, [1, 2])
        sheet = check.Sheet(packet, reading=check.Reading(marks=[check.Mark("99215", "visit", "visit", (1, 2, 3, 4), "circled")]))
        result = check.BatchResult("/tmp/AB 10.06.26.pdf", None, None, 2, [], [], [sheet])
        row = check.manual_rows([result])[0]
        self.assertNotIn("codes", row)
        self.assertNotIn("finding", row)
        self.assertEqual(row["source_page"], 2)

    def test_handwritten_date_matching_tolerates_common_ocr_letters(self):
        day = date(2026, 10, 6)
        self.assertEqual(check.date_matches("1O/O6/2O26", day), "full")
        self.assertEqual(check.date_matches("1006/2026", day), "full")
        self.assertEqual(check.date_matches("10/06/2025", day), "partial")
        self.assertEqual(check.date_matches("11/06/2026", day), "")
        self.assertIsNone(check._agreed_date_read(["10/06/2020", "10/06/2020"]))
        self.assertEqual(check._agreed_date_read(["10/06/2020"] * 3), (10, 6, 2020))
        self.assertTrue(check._confusable_year(2020, 2026))
        self.assertFalse(check._confusable_year(2025, 2026))
        self.assertTrue(check._ambiguous_day_read("1016 26", day, 10, 16))
        self.assertFalse(check._ambiguous_day_read("10/16/26", day, 10, 16))


if __name__ == "__main__":
    unittest.main()
