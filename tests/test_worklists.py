"""Today screen and auth worklist rules, on a made-up clinic day."""
import json
import os
import tempfile
import unittest
from datetime import date

from recordfinder import outlook_cache, worklists
from recordfinder.index import Store

DAY = date(2026, 10, 5)
# (entry id, calendar, time, subject, categories, notes)
BOOKINGS = [
    ("a1", "Touraj", "09:00", "Sample, Alice 12340 DOB: 01/02/1950 Ph: 619 555 0100 Ins: SCMG/UHC ID: A1", "Follow-Up",
     "Authorization #234211 9/2/2026 - 3/1/2027\n99215 (2)"),
    ("a2", "VNG_VEMP", "10:00", "Sample, Alice 12340 DOB: 01/02/1950 Ins: SCMG/UHC ID: A1", "", ""),   # test, visit-only auth
    ("b1", "Touraj", "09:30", "Sample, Bob DOB: 03/04/1960 Ins: SCMG ID: B1", "New Patient", "Need Chair Auth"),  # no auth
    ("c1", "Jake Perkins", "11:00", "Sample, Carol 55550 DOB: 05/06/1970 Ins: Scripps ID: C1", "Follow-Up",
     "Authorization Number: 21841 1/2/26-6/30/26"),                                                         # expired
    ("d1", "Jake Perkins", "11:30", "Sample, Dan 77770 DOB: 07/08/1945 Ins: MCR ID: D1", "Follow-Up, ABN Form", ""),
    ("e1", "Carter Copeland", "12:00", "Lunch w/ Rep Sample, Rep", "", ""),                                   # block
    ("f1", "Carter Copeland", "13:00", "Sample, Fay DOB: 09/10/1980 Ins: Aetna ID: ??", "Cancelled", ""),     # cancelled
    ("g1", "Carter Copeland", "14:00", "Sample, Gus 99990 DOB: 11/12/1985 Ins: SCMG ID: G1", "Follow-Up",
     "Authorization #500001 9/1/2026 - 10/10/2026\n99214 (1)"),                                              # ends soon
]


class WorklistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "index.db"))
        outlook_cache.ensure_schema(self.store)
        rows, self.notes = [], {}
        for eid, cal, hhmm, subj, cats, note in BOOKINGS:
            info = outlook_cache.parse_subject(subj)
            lk, fk, nk = outlook_cache._keys(info)
            start = f"{DAY.isoformat()}T{hhmm}"
            rows.append((eid, cal, start, start, subj, cats, lk, fk, nk, info["dob"].isoformat() if info["dob"] else "",
                         info["account"], info["insurance"], info["secondary"], "2026-09-30T00:00:00"))
            self.notes[eid] = note
        outlook_cache._flush(self.store, rows)
        vd = os.path.join(self.tmp.name, "vd")
        os.makedirs(vd)
        run = [{"pt": "77770", "ok": True, "self": 58.04, "insur": 0, "total": 58.04, "self_aging": [0, 0, 0, 58.04, 58.04],
                "insur_aging": [0, 0, 0, 0, 0], "rows": []},
               {"pt": "55550", "ok": True, "self": -40.0, "insur": 0, "total": -40.0, "self_aging": [0] * 5,
                "insur_aging": [0] * 5, "rows": []}]
        path = os.path.join(vd, "results_1002.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r) for r in run))
        from recordfinder import balances
        balances.import_folder(self.store, vd)
        self.reads = []

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def read(self, eid):
        self.reads.append(eid)
        return self.notes[eid]

    def rows(self):
        return {r.entry_id: r for r in worklists.day(self.store, DAY, read_body=self.read)}

    def test_day(self):
        rows = self.rows()
        self.assertNotIn("f1", rows)                                     # cancelled visits are left out
        self.assertFalse(rows["e1"].patient)                             # the rep lunch is a block, not a patient
        self.assertEqual(rows["a1"].auth_status, "OK")
        self.assertEqual(rows["a2"].auth_status, "Test not on auth")     # VNG on an auth that lists only 99215
        self.assertEqual(rows["b1"].auth_status, "No auth")
        self.assertIn(("Need Chair Auth", "amber"), rows["b1"].alerts)
        self.assertEqual(rows["c1"].auth_status, "Expired")
        self.assertIn(("Credit $40.00", "green"), rows["c1"].alerts)
        self.assertEqual(rows["g1"].auth_status, "Ends soon")
        self.assertFalse(rows["d1"].auth_needed)                          # Medicare: no group auth
        self.assertIn(("Self $58.04", "red"), rows["d1"].alerts)          # over 90 days -> red
        self.assertIn(("Over 90 days", "red"), rows["d1"].alerts)
        self.assertIn(("ABN form", "blue"), rows["d1"].alerts)
        self.assertEqual(rows["d1"].visit, "Follow-Up")                   # ABN Form isn't the visit type
        self.assertTrue(rows["a2"].needs_attention)
        self.assertFalse(rows["a1"].needs_attention)

    def test_notes_read_once(self):
        self.rows()
        first = sorted(self.reads)
        self.reads.clear()
        self.rows()
        self.assertEqual(self.reads, [])                                  # the second look is all cached
        self.assertEqual(first, sorted({"a1", "a2", "b1", "c1", "g1"}))  # only HMO / group patients are read

    def test_auth_worklist(self):
        got = worklists.auth_worklist(self.store, DAY, 1, read_body=self.read)
        self.assertEqual(sorted(r.entry_id for r in got), ["a1", "a2", "b1", "c1", "g1"])

    def test_pull_list(self):
        text = worklists.pull_list(worklists.day(self.store, DAY, read_body=self.read))
        self.assertIn("Charts to pull - Monday 10/05/2026", text)
        self.assertIn("Touraj (2)", text)
        self.assertLess(text.index("Sample, Alice"), text.index("Sample, Bob"))   # appointment order
        self.assertNotIn("Rep", text)
        self.assertNotIn("Fay", text)


if __name__ == "__main__":
    unittest.main()
