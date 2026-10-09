"""Chart data: Veradigm balance snapshots and authorizations from appointment notes. Made-up data only."""
import json
import os
import tempfile
import time
import unittest
from datetime import date

from recordfinder import auths, balances, outlook_cache
from recordfinder.index import Store
from recordfinder.record_ui import group_of, header_facts
from recordfinder.outlook_cache import Booking
from datetime import datetime

RUN = [
    {"i": 0, "last": "Sample", "first": "Alice", "pt": "012340", "ok": True, "self": 40.0, "insur": 570.0, "total": 610.0,
     "unassigned": 20.0, "self_aging": [40.0, 0, 0, 0, 40.0], "insur_aging": [0, 0, 0, 570.0, 570.0],
     "rows": [{"svc": "08/31/2026", "voucher": "111", "provider": "DOCTOR", "chg": "$350.00", "pmts": "$10.00",
               "balance": "$340.00", "payor": "SCMG", "coverage": "Medical", "billed": "09/03/2026", "age": "25", "_row": 0}]},
    {"i": 1, "last": "Sample", "first": "Bob", "pt": "55550", "ok": True, "self": -45.0, "insur": 0, "total": -45.0,
     "self_aging": [0, 0, 0, 0, 0], "insur_aging": [0, 0, 0, 0, 0], "rows": []},
    {"i": 2, "last": "Nobody", "first": "Found", "pt": "", "ok": False},
]

NOTES = {
    "e1": "Authorization #234211 9/2/2026 - 3/1/2027\n99205 (1)\n99215 (2)\n*MF",
    "e2": "auth printed*mr\nAuthorization #234211 9/2/2026 - 3/1/2027\n95992 (1)",      # same auth copied, more codes
    "e3": "Authorization Number: 21841 7/17/25-1/13/26\nNeed Chair Auth",                    # expired + a note
    "e4": "Auth 40121-09/11/26-03/10/27 *IC",
    "e5": "Rep lunch, no auth here",
}


class BalanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "index.db"))
        self.folder = os.path.join(self.tmp.name, "vd")
        os.makedirs(self.folder)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def write(self, name, rows, when):
        path = os.path.join(self.folder, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r) for r in rows))
        os.utime(path, (when, when))

    def test_import_and_latest(self):
        day1 = time.mktime((2026, 9, 29, 22, 0, 0, 0, 0, -1))
        self.write("results_0929.jsonl", RUN, day1)
        self.assertEqual(balances.import_folder(self.store, self.folder), 2)     # the not-found patient is skipped
        self.assertEqual(balances.import_folder(self.store, self.folder), 0)     # unchanged files aren't re-read
        b = balances.latest(self.store, "12340")                                  # leading zeros don't matter
        self.assertEqual((b.self, b.insur, b.as_of), (40.0, 570.0, date(2026, 9, 29)))
        self.assertEqual(b.insur_over_90, 570.0)
        self.assertEqual(b.items[0]["payor"], "SCMG")
        self.assertTrue(balances.latest(self.store, "55550").credit)
        day2 = time.mktime((2026, 9, 30, 22, 0, 0, 0, 0, -1))
        self.write("results_0930.jsonl", [dict(RUN[0], self=0.0, total=570.0)], day2)
        balances.import_folder(self.store, self.folder)
        self.assertEqual(balances.latest(self.store, "012340").self, 0.0)           # the newest check wins
        self.assertEqual([h.as_of.day for h in balances.history(self.store, "12340")], [30, 29])
        self.assertEqual(balances.latest_totals(self.store)["12340"][0], 0.0)
        self.assertIsNone(balances.latest(self.store, "999"))


class AuthTests(unittest.TestCase):
    def test_parse(self):
        found, notes = auths.parse(NOTES["e3"])
        self.assertEqual((found[0].number, found[0].start, found[0].end), ("21841", date(2025, 7, 17), date(2026, 1, 13)))
        self.assertEqual(notes, ["Need Chair Auth"])
        found, _ = auths.parse(NOTES["e1"])
        self.assertEqual(found[0].codes, ["99205 (1)", "99215 (2)"])
        self.assertEqual(found[0].status(date(2026, 9, 1)), "Upcoming")
        self.assertEqual(found[0].status(date(2026, 10, 1)), "Active")
        self.assertEqual(found[0].status(date(2027, 3, 2)), "Expired")

    def test_needs_auth(self):
        self.assertTrue(auths.needs_auth("SCMG/UHC"))
        self.assertTrue(auths.needs_auth("UHC HMO"))
        self.assertFalse(auths.needs_auth("MCR Health Net"))       # Health Net behind Medicare is the secondary
        self.assertFalse(auths.needs_auth("Aetna PPO"))
        self.assertFalse(auths.needs_auth("Cash Pay"))
        self.assertTrue(auths.needs_auth("MCR", "SCMG"))

    def test_for_patient_reads_each_note_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(os.path.join(tmp, "index.db"))
            outlook_cache.ensure_schema(store)
            rows = []
            for k, eid in enumerate(NOTES):
                start = f"2026-09-{10 + k:02d}T09:00"
                rows.append((eid, "EC", start, start, "Sample, Alice DOB: 01/02/1990", "", "SAMPLE", "ALICE", "",
                             "1990-01-02", "", "", "", "2026-09-01T00:00:00"))
            outlook_cache._flush(store, rows)
            reads = []

            def read(eid):
                reads.append(eid)
                return NOTES[eid]
            found, notes = auths.for_patient(store, "SAMPLE", "ALICE", "1990-01-02", "", read_body=read)
            self.assertEqual(sorted(reads), sorted(NOTES))
            self.assertEqual([a.number for a in found], ["40121", "234211", "21841"])      # newest end date first
            self.assertEqual(found[1].codes, ["99205 (1)", "99215 (2)", "95992 (1)"])       # merged from both visits
            self.assertEqual(notes[0][1], "Need Chair Auth")
            reads.clear()
            auths.for_patient(store, "SAMPLE", "ALICE", "1990-01-02", "", read_body=read)
            self.assertEqual(reads, [])                                                     # cached
            store.close()


class ChartTests(unittest.TestCase):
    def test_header_facts_newest_first(self):
        b = lambda d, s: Booking("EC", datetime(2026, 9, d, 9), datetime(2026, 9, d, 10), s)  # noqa: E731
        facts = header_facts([
            b(1, "Sample, Alice 12340 DOB: 01/02/1990 Ph: 619 555 0100 Ins: SCMG/UHC ID: ABC12345 Sec: MCR ID: 1AA2BB3CC44 "
                 "alice@example.com Cc: Vertigo Ref: Doe, John MD Fax: 619 555 0199 *IC"),
            b(20, "Sample, Alice DOB: 01/02/1990 Ph: 619 555 0111 Ins: SCMG/UHC"),
        ])
        self.assertEqual(facts["phone"], "619 555 0111")          # the newest header's phone
        self.assertEqual(facts["id1"], "ABC12345")                 # older header fills what the newest lacks
        self.assertEqual(facts["id2"], "1AA2BB3CC44")
        self.assertEqual(facts["email"], "alice@example.com")
        self.assertEqual(facts["fax"], "619 555 0199")
        self.assertEqual(facts["account"], "12340")
        self.assertEqual(facts["complaint"], "Vertigo")

    def test_groups(self):
        self.assertEqual(group_of("VNG"), "Vestibular testing")
        self.assertEqual(group_of("EMG/NCV (same name)"), "Neuro testing")
        self.assertEqual(group_of("Auth letter"), "Referrals & authorizations")
        self.assertEqual(group_of("File"), "Other files")


if __name__ == "__main__":
    unittest.main()
