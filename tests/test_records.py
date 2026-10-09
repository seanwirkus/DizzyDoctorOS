"""Outlook cache, progress notes and whole-day superbill alignment. All data here is made up."""
import os
import tempfile
import unittest
from datetime import date, datetime

from recordfinder import notes, outlook_cache, superbills
from recordfinder.index import Store
from recordfinder.superbills import PatientQuery

GRID = "\n".join(f"{c} Some procedure" for c in (99204, 99205, 99213, 99214, 99215, 92285, 92541, 92542, 92546, 95992,
                                                  95886, 95908, 64405, 20552)) + "\n" + \
       " ".join(f"H{n:02}.{n % 9}" for n in range(10, 30)) + " R51.9 G43.909 R42 H81.10"


class OutlookCacheTests(unittest.TestCase):
    def test_find_and_summary(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(os.path.join(root, "index.db"))
            try:
                outlook_cache.ensure_schema(store)
                subj = [("A Brown", "2026-09-24T10:00", "#AB Sample, Alice 'Ally' DOB: 01/02/1990 Ins: UHC ID: 1", "Follow-Up"),
                        ("A Brown", "2026-08-01T09:00", "Sample, Alice DOB: 01/02/1990 Ins: UHC", "No Show"),
                        ("Cancellations_No Show's", "2026-07-01T09:00", "Sample, Alice DOB: 01/02/1990", ""),
                        ("D Reed", "2026-09-24T11:00", "Sample, Bob DOB: 03/04/1950 Acc # 12345 Ins: MCR", "New Patient"),
                        ("D Reed", "2026-09-25T08:00", "Lunch w/ Rep Sample, Rep", "")]
                rows = []
                for k, (cal, start, s, cats) in enumerate(subj):
                    info = outlook_cache.parse_subject(s)
                    lk, fk, nk = outlook_cache._keys(info)
                    rows.append((f"id{k}", cal, start, start, s, cats, lk, fk, nk, info["dob"].isoformat() if info["dob"] else "",
                                 info["account"], info["insurance"], info["secondary"], start))
                outlook_cache._flush(store, rows)
                self.assertEqual(len(outlook_cache.find_patient(store, "Sample", "Ally")), 1)  # only the booking with the nickname
                alice = outlook_cache.find_patient(store, "Sample", "Alice")
                self.assertEqual([b.start.date() for b in alice], [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 24)])
                self.assertEqual([b.seen for b in alice], [False, False, True])
                self.assertEqual(outlook_cache.summary(alice)["dobs"], {"1990-01-02": 3})
                self.assertEqual(outlook_cache.summary(alice)["insurance"], "UHC")
                self.assertEqual(len(outlook_cache.find_patient(store, account="012345")), 1)       # leading zero ignored
                self.assertEqual(len(outlook_cache.find_patient(store, "Sample", dob=date(1950, 3, 4))), 1)
                self.assertEqual(len(outlook_cache.day(store, date(2026, 9, 24))), 2)
            finally:
                store.close()


class NoteNameTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(notes.parse_note_name("SampleAlice 09 24 26.pdf"), ("SampleAlice", date(2026, 9, 24)))
        self.assertEqual(notes.parse_note_name("SampleJr.Bob 09 22 26.pdf"), ("SampleJr.Bob", date(2026, 9, 22)))
        self.assertEqual(notes.parse_note_name("TesterCarl 09 24 25.pdf")[1], date(2025, 9, 24))
        self.assertTrue(notes.matches_patient("SampleJr.Bob", "Sample", "Bob"))
        self.assertTrue(notes.matches_patient("VanDerSampleFrank", "Van Der Sample", "Frank"))
        self.assertFalse(notes.matches_patient("SampletonRick", "Sample", "Alice"))

    def test_all_configured_years_are_reconciled_even_before_drive_reindex(self):
        with tempfile.TemporaryDirectory() as root:
            year = os.path.join(root, "2018")
            os.makedirs(year)
            path = os.path.join(year, "SampleAlice 03 04 18.pdf")
            with open(path, "wb") as f:
                f.write(b"placeholder")
            store = Store(os.path.join(root, "index.db"))
            try:
                found = notes.find_notes(store, {"notes_folders": [root]}, "Sample", "Alice")
            finally:
                store.close()
        self.assertEqual([(n.dos, n.kind) for n in found], [(date(2018, 3, 4), "final")])


def batch(order):
    """A fake batch: schedule page, then per patient a superbill (+ their card when given)."""
    texts = ["schedule"]
    for card in order:
        texts.append(GRID)
        if card:
            texts.append(card)
    return texts


class AlignDayTests(unittest.TestCase):
    PATS = [PatientQuery("Alice", "Sample", dob=date(1990, 1, 2)), PatientQuery("Bob", "Tester"), PatientQuery("Carl", "Example"),
            PatientQuery("Dana", "Fake", dob=date(1950, 3, 4))]

    def test_missing_superbill_does_not_shift_everyone(self):
        # Bob was booked but has no superbill; Carl's packet has nothing printed
        texts = batch(["CARD Member ALICE SAMPLE", None, "CARD DOB 03/04/1950"])
        placed = superbills.align_day(texts, self.PATS, weights=[1, 0.25, 1, 1])
        pages = [p.packet.superbill if p.packet else None for p in placed]
        self.assertEqual(pages, [1, None, 3, 4])
        self.assertEqual([p.how for p in placed], ["printed", "none", "order", "printed"])

    def test_same_time_patients_are_tied_without_evidence(self):
        texts = batch([None, None])
        placed = superbills.align_day(texts, self.PATS[1:3], groups=["10:00", "10:00"])
        self.assertEqual({p.how for p in placed}, {"tie"})
        texts = batch([None, "CARD Member CARL EXAMPLE"])       # Carl's card decides the order
        placed = superbills.align_day(texts, self.PATS[1:3], groups=["10:00", "10:00"])
        self.assertEqual([p.packet.superbill for p in placed], [1, 2])
        self.assertEqual([p.how for p in placed], ["order", "printed"])

    def test_common_word_names_need_more_than_the_surname(self):
        q = PatientQuery("Sharon", "May")
        texts = ["schedule", GRID, "You may bring a list of questions"]
        _c, packets = superbills.split_packets(texts)
        self.assertLess(superbills.score_packet(texts, packets[0], q)[0], 30)
        texts[2] = "Member MAY SHARON"
        self.assertGreaterEqual(superbills.score_packet(texts, packets[0], q)[0], 30)


if __name__ == "__main__":
    unittest.main()
