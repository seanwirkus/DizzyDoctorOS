"""MIPS 2026: reading the measure workbooks, the office's status per measure, and reminders on visits (made-up data)."""
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta

from recordfinder import mips, outlook_cache, worklists
from recordfinder.index import Store

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None

HEAD = ["Provider Last Name", "Provider First Name", "Individual NPI", "TIN", None, None, "Last Name", "First Name", "MI",
        "DOB", "Gender", "MRN", "Visit Date", "Result", "Denominator"]
NAMES = {"126": "2026 - 126_Diabetes_Test.xlsx", "268": "2026 - 268_Epilepsy_Test.xlsx", "277": "2026 - 277_Sleep_Test.xlsx",
         "291": "2026 - 291_Parkinson_Cog_Test.xlsx", "293": "2026 - 293_Parkinson_Rehab_Test.xlsx"}


def _book(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEAD)
    for last, first, dob, visit, result in rows:
        ws.append(["Purcell", "Ian", "0000000000", "000000000", "TY", "", last, first, "", dob, "Female", "", visit, result, "Yes"])
    ws.append([None, None, None, None, None, "Notes"])          # an office note line: not a patient
    wb.save(path)


@unittest.skipIf(openpyxl is None, "openpyxl not installed")
class MipsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        _book(os.path.join(d, NAMES["126"]), [("Sample", "Alice", datetime(1950, 1, 2), datetime(2026, 3, 1), "Done"),
                                              ("Doe", "Jane", datetime(1960, 5, 6), datetime(2026, 4, 1), "")])
        _book(os.path.join(d, NAMES["268"]), [("Roe", "Rita", "07/08/1990", "05/05/2026", "Done")])
        _book(os.path.join(d, NAMES["277"]), [("Poe", "Pat", datetime(1955, 1, 1), datetime(2026, 2, 2), "Not Done - Documented Reason")])
        _book(os.path.join(d, NAMES["291"]), [("Doe", "Jane", datetime(1960, 5, 6), datetime(2026, 4, 1), "Not Done"),
                                              ("Moe", "Max", datetime(1940, 2, 3), datetime(2026, 6, 1), "Patient or care partner decline assessment")])
        _book(os.path.join(d, NAMES["293"]), [("Moe", "Max", datetime(1940, 2, 3), datetime(2026, 6, 1), "Done?")])
        mips._cache.clear()
        self.store = Store(os.path.join(d, "index.db"))
        outlook_cache.ensure_schema(self.store)
        soon = datetime.now() + timedelta(days=3)
        with self.store._lock:
            self.store.db.executemany("INSERT INTO appts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                ("e1", "Touraj", soon.isoformat(timespec="minutes"), soon.isoformat(timespec="minutes"),
                 "Doe, Jane DOB: 05/06/1960 Ins: MCR ID: X", "Follow-Up", "DOE", "JANE", "", "1960-05-06", "", "MCR", "", ""),
                ("e2", "Touraj", soon.isoformat(timespec="minutes"), soon.isoformat(timespec="minutes"),
                 "Roe, Rita DOB: 07/08/1990 Ins: UHC ID: Y", "Follow-Up", "ROE", "RITA", "", "1990-07-08", "", "UHC", "", ""),
            ])
            self.store.db.commit()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_classify(self):
        self.assertEqual(mips.classify("Done"), "met")
        self.assertEqual(mips.classify(""), "pending")
        self.assertEqual(mips.classify("Not Done"), "not met")
        self.assertEqual(mips.classify("Not Done - Medical Reason"), "reason")
        self.assertEqual(mips.classify("Patient already receiving physical, occupational therapy"), "reason")
        self.assertEqual(mips.classify("Done?"), "check")

    def test_status(self):
        st = {s.measure.number: s for s in mips.status(self.tmp.name)}
        self.assertEqual((st["126"].rows, st["126"].met, st["126"].pending), (2, 1, 1))     # the note line isn't a row
        self.assertEqual(st["126"].completeness, 0.5)
        self.assertIn("completeness 50% (needs 75%)", st["126"].problems)
        self.assertIn("only 2 cases (needs 20)", st["126"].problems)
        self.assertEqual(st["277"].rate, None)                  # only a documented reason: no performance rate yet
        self.assertEqual((st["291"].not_met, st["291"].reason, st["291"].rate), (1, 1, 0.0))
        self.assertEqual(st["293"].check, 1)

    def test_todo_and_next_visit(self):
        items = mips.todo(self.store, self.tmp.name)
        got = [(t.measure.number, t.entry.last, t.next_visit is not None) for t in items]
        # Jane: blank #126 and a "Not Done" #291 (once a year: still fixable) - both with her visit in 3 days, first
        self.assertEqual(got[:2], [("126", "Doe", True), ("291", "Doe", True)])
        self.assertIn(("293", "Moe", False), got)                 # "Done?" needs a look; no visit booked
        self.assertNotIn(("277", "Poe", False), got)               # a documented reason is a final answer
        self.assertFalse(any(n == "268" for n, _l, _v in got))     # Rita's 268 is Done

    def test_reminders_on_the_visit(self):
        mips.FOLDER = self.tmp.name
        try:
            rows = worklists.annotate(self.store, worklists._rows(self.store, date.today(), date.today() + timedelta(days=5)),
                                      read_body=lambda eid: "")
        finally:
            mips.FOLDER = mips.DEFAULT_FOLDER
        by = {r.last: r for r in rows}
        self.assertEqual([m.number for m in by["Doe"].mips], ["126", "291"])
        self.assertIn(("MIPS 291 MoCA", "amber"), by["Doe"].alerts)
        self.assertEqual([m.number for m in by["Roe"].mips], ["268"])    # counseling boxes at EVERY visit


if __name__ == "__main__":
    unittest.main()
