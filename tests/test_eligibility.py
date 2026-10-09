"""Eligibility: portal per plan, recorded checks, portal-run imports. Made-up patients only."""
import json
import os
import tempfile
import unittest
from datetime import date, timedelta

from recordfinder import eligibility, outlook_cache, worklists
from recordfinder.index import Store


class PortalTests(unittest.TestCase):
    def test_portal_for(self):
        p = eligibility.portal_for
        self.assertEqual(p("SCMG/UHC"), "Sharp Link")
        self.assertEqual(p("MCR", "SCMG"), "Sharp Link")                 # the medical group's portal wins
        self.assertEqual(p("Scripps/ Anthem"), "Scripps Care Link")
        self.assertEqual(p("MCR"), "Noridian")
        self.assertEqual(p("Medi-Medi"), "Noridian")
        self.assertEqual(p("Anthem BC"), "Availity")
        self.assertEqual(p("BCBS PPO"), "Availity")
        self.assertEqual(p("Aetna"), "Availity")
        self.assertEqual(p("UHC Medicare Advantage"), "UHC Provider portal")
        self.assertEqual(p("UMR"), "UMR portal")
        self.assertEqual(p("Cigna PPO"), "Cigna for HCP")
        self.assertEqual(p("Tricare for Life"), "UHC Military West (Tricare)")
        self.assertEqual(p("Cash Pay"), "Cash pay - nothing to verify")
        self.assertTrue(p("").startswith("Unknown"))


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "index.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_record_and_latest(self):
        eligibility.record(self.store, "SAMPLE", "1950-01-02", "Active", plan="MCR", by="*IC",
                           when=date.today() - timedelta(days=40))
        old = eligibility.latest(self.store, "SAMPLE", "1950-01-02")
        self.assertFalse(old.fresh)                                        # 40 days old: due again
        eligibility.record(self.store, "SAMPLE", "1950-01-02", "Problem", detail="termed 09/30", by="*IC")
        new = eligibility.latest(self.store, "SAMPLE", "1950-01-02")
        self.assertEqual((new.status, new.detail, new.fresh), ("Problem", "termed 09/30", True))
        self.assertTrue(new.label().startswith("Problem"))
        with self.assertRaises(ValueError):
            eligibility.record(self.store, "SAMPLE", "1950-01-02", "Maybe")

    def test_import_portal_run(self):
        folder = os.path.join(self.tmp.name, "elig")
        os.makedirs(folder)
        rows = [{"last": "De La Sample", "first": "Maria", "dob": "09/10/1955", "status": "active", "plan": "MCR",
                 "checked": "09/30/2026", "portal": "Noridian"},
                {"last": "Sample", "dob": "bad", "status": "Active"},             # unusable: skipped
                {"last": "Sample", "dob": "01/02/1950", "status": "Unknown"}]     # not a status we keep: skipped
        with open(os.path.join(folder, "elig_20260930.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r) for r in rows))
        self.assertEqual(eligibility.import_folder(self.store, folder), 1)
        self.assertEqual(eligibility.import_folder(self.store, folder), 0)           # not re-read
        c = eligibility.latest(self.store, "DELASAMPLE", "1955-09-10")
        self.assertEqual((c.status, c.checked, c.source), ("Active", date(2026, 9, 30), "portal run: Noridian"))

    def test_today_rows_show_it(self):
        outlook_cache.ensure_schema(self.store)
        day = date.today() + timedelta(days=1)
        subj = "Sample, Alice DOB: 01/02/1950 Ins: MCR ID: 1AA2BB3CC44"
        info = outlook_cache.parse_subject(subj)
        lk, fk, nk = outlook_cache._keys(info)
        start = f"{day.isoformat()}T09:00"
        outlook_cache._flush(self.store, [("x1", "Touraj", start, start, subj, "", lk, fk, nk, "1950-01-02", "", "MCR", "",
                                           "2026-09-30T00:00:00")])
        (r,) = worklists.day(self.store, day, read_body=lambda e: "")
        self.assertEqual((r.portal, r.elig), ("Noridian", None))
        eligibility.record(self.store, lk, "1950-01-02", "Active", by="*IC")
        (r,) = worklists.day(self.store, day, read_body=lambda e: "")
        self.assertIn(("Elig OK", "green"), r.alerts)
        text = worklists.eligibility_list([r])
        self.assertIn("Noridian (1)", text)
        self.assertIn("ID 1AA2BB3CC44", text)
        self.assertIn("Active", text)


if __name__ == "__main__":
    unittest.main()
