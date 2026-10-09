"""Patient directory, file linking and the Claude spend limit. All names and dates here are made up."""
import ntpath
import os
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace

from recordfinder import ai, outlook_cache, people
from recordfinder.index import Store

BOOKINGS = [
    ("EC", "2026-09-24T10:00", "Sample, Alice DOB: 01/02/1990 Ins: UHC ID: 1"),
    ("EC", "2025-03-01T10:00", "#AB Sample, Alice DOB: 01/02/1990 Ins: UHC"),
    ("TY", "2026-06-01T09:00", "Sample, Bob 12345 DOB: 03/04/1950 Ins: MCR"),     # two Bob Samples ...
    ("TY", "2019-02-01T09:00", "Sample, Bob DOB: 05/06/1972 Ins: Aetna"),        # ... told apart by DOB
    ("JP", "2026-09-25T08:00", "Lunch w/ Rep Sample, Rep"),                      # no DOB: not a patient
    ("JP", "2026-09-26T08:00", "Tester, Nancy DOB: 07/08/1960 Ins: BC"),
    ("JP", "2026-09-27T08:00", "De La Sample, Maria DOB: 09/10/1955 Ins: MCR"),
]
FILES = [
    r"V:\1111 Progress Notes\2026\SampleAlice 09 24 26.pdf",       # LastFirst
    r"V:\1111C Chair Reports\Sample, Alice 09 24 26.pdf",          # Last, First
    r"V:\1111 Progress Notes\2025\SampleA 03 01 25.pdf",           # LastF
    r"V:\Faxes\TY\TY Sample Alice Walmart Refill.pdf",             # fax label
    r"V:\JH\Sample, Alice\visit 1.pdf",                            # per-patient folder
    r"V:\EMG NCV Reports\SampleBob 06 01 26.pdf",                  # Bob, visit date only fits the 1950 Bob
    r"V:\Faxes\Sample Bob 05 06 1972 records.pdf",                 # Bob with DOB -> the 1972 Bob
    r"V:\Faxes\Sample Bob records.pdf",                            # no way to tell -> both, "same name"
    r"V:\Prepped notes\TesterNancy 09 26 26.pdf",                  # "rn" across the name boundary
    r"V:\1111 Progress Notes\2026\De la SampleMaria 09 27 26.pdf", # multi-word last name
    r"V:\HR\Sample, Alice W-4.pdf",                                # staff files are never linked
    r"V:\Faxes\Referral Notes.pdf",                                # not a patient
    r"V:\1111 Progress Notes\2026\SampleAlicia 09 24 26.pdf",     # first-name typo, same day as Alice's visit
    r"V:\1111 Progress Notes\2026\SampleAlicia 01 05 26.pdf",     # same typo, no visit that day: not Alice
    r"V:\1111 Progress Notes\2026\TesterNancyB 09 26 26.pdf",     # middle initial on the end
    r"V:\1111 Progress Notes\2004\OldmanWalter 03 15 04.pdf",     # never in Outlook: a files-only entry
    r"V:\1111 Progress Notes\2004\Oldman, Walter 04 20 04.pdf",
]


class PeopleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "index.db"))
        outlook_cache.ensure_schema(self.store)
        rows = []
        for k, (cal, start, s) in enumerate(BOOKINGS):
            info = outlook_cache.parse_subject(s)
            lk, fk, nk = outlook_cache._keys(info)
            rows.append((f"id{k}", cal, start, start, s, "", lk, fk, nk, info["dob"].isoformat() if info["dob"] else "",
                         info["account"], info["insurance"], info["secondary"], start))
        outlook_cache._flush(self.store, rows)
        with self.store._lock:
            self.store.db.executemany("INSERT INTO files VALUES(?,?,?,?,?)",
                                      [(p, ntpath.basename(p), ntpath.dirname(p), 1, 0.0) for p in FILES])
            self.store.db.commit()
        self.n, self.links = people.build(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def files(self, last, first, dob):
        (p,) = people.lookup(self.store, last, first, date.fromisoformat(dob))
        return {ntpath.basename(r[0]): r[5] for r in people.files_for(self.store, p.id)}

    def test_directory(self):
        self.assertEqual(self.n, 7)  # Alice, two Bobs, Nancy, Maria, and from files only: Alicia and Walter
        alice = people.lookup(self.store, "Sample", "Alice")[0]
        self.assertEqual((alice.last, alice.first, alice.bookings), ("Sample", "Alice", 2))
        self.assertEqual((alice.first_visit, alice.last_visit), (date(2025, 3, 1), date(2026, 9, 24)))

    def test_naming_styles(self):
        alice = self.files("Sample", "Alice", "1990-01-02")
        self.assertEqual(set(alice), {"SampleAlice 09 24 26.pdf", "Sample, Alice 09 24 26.pdf", "SampleA 03 01 25.pdf",
                                      "TY Sample Alice Walmart Refill.pdf", "visit 1.pdf", "SampleAlicia 09 24 26.pdf"})
        self.assertTrue(alice["visit 1.pdf"].startswith("folder"))
        self.assertEqual(alice["SampleA 03 01 25.pdf"], "initial")
        self.assertIn("TesterNancy 09 26 26.pdf", self.files("Tester", "Nancy", "1960-07-08"))
        self.assertIn("TesterNancyB 09 26 26.pdf", self.files("Tester", "Nancy", "1960-07-08"))
        self.assertEqual(alice["SampleAlicia 09 24 26.pdf"], "close name (visit day)")
        self.assertNotIn("SampleAlicia 01 05 26.pdf", alice)
        self.assertIn("De la SampleMaria 09 27 26.pdf", self.files("De La Sample", "Maria", "1955-09-10"))

    def test_same_name(self):
        old, young = self.files("Sample", "Bob", "1950-03-04"), self.files("Sample", "Bob", "1972-05-06")
        self.assertIn("SampleBob 06 01 26.pdf", old)
        self.assertNotIn("SampleBob 06 01 26.pdf", young)
        self.assertIn("Sample Bob 05 06 1972 records.pdf", young)
        self.assertNotIn("Sample Bob 05 06 1972 records.pdf", old)
        self.assertIn("same name", old["Sample Bob records.pdf"])
        self.assertIn("same name", young["Sample Bob records.pdf"])

    def test_files_only_patients(self):
        (walter,) = people.search(self.store, "oldman")
        self.assertEqual((walter.last, walter.first, walter.dob, walter.bookings, walter.files), ("Oldman", "Walter", None, 0, 2))
        self.assertEqual((walter.first_visit, walter.last_visit), (date(2004, 3, 15), date(2004, 4, 20)))
        self.assertEqual(len(people.files_for(self.store, walter.id)), 2)

    def test_never_linked(self):
        linked = {r[0] for r in self.store.reader().execute("SELECT path FROM file_links")}
        self.assertNotIn(r"V:\HR\Sample, Alice W-4.pdf", linked)
        self.assertNotIn(r"V:\Faxes\Referral Notes.pdf", linked)

    def test_search(self):
        names = lambda q: sorted(f"{p.first} {p.last}" for p in people.search(self.store, q))  # noqa: E731
        self.assertEqual(names("samp"), ["Alice Sample", "Alicia Sample", "Bob Sample", "Bob Sample"])
        self.assertEqual(names("Sample, Al"), ["Alice Sample", "Alicia Sample"])
        self.assertEqual(names("Sample, Alice"), ["Alice Sample"])
        self.assertEqual(names("alice sample"), ["Alice Sample"])
        self.assertEqual(names("nancy"), ["Nancy Tester"])
        self.assertEqual(names("de la sample"), ["Maria De La Sample"])
        self.assertEqual(names("03/04/1950"), ["Bob Sample"])
        self.assertEqual(names("3/4/50"), ["Bob Sample"])
        self.assertEqual(names("012345"), ["Bob Sample"])
        self.assertEqual(len(people.search(self.store, "")), 7)

    def test_kind(self):
        self.assertEqual(people.kind_of(r"V:\EMG NCV Reports\x.pdf"), "EMG/NCV")
        self.assertEqual(people.kind_of(r"V:\1111C Chair Reports\x.pdf"), "Chair report")
        self.assertEqual(people.kind_of(r"V:\Zeto Reports\Sample Alice.pdf"), "EEG")
        self.assertEqual(people.kind_of(r"V:\Faxes\TY\x.pdf"), "Fax")


class MeterTests(unittest.TestCase):
    def test_limit(self):
        box = {}
        m = ai.Meter(box.get, box.__setitem__, 1.00, today=lambda: date(2026, 9, 30))
        usage = SimpleNamespace(input_tokens=100_000, output_tokens=20_000, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        self.assertAlmostEqual(m.add(ai.MODEL, usage), 0.80)            # 0.1M x $4 + 0.02M x $20
        m.check()                                                        # $0.80 of $1.00: still allowed
        self.assertAlmostEqual(m.add(ai.FAST_MODEL, usage), 0.40)       # Sonnet: half the price
        with self.assertRaises(ai.AIError):
            m.check()
        tomorrow = ai.Meter(box.get, box.__setitem__, 1.00, today=lambda: date(2026, 10, 1))
        tomorrow.check()                                                 # a new day starts at $0
        self.assertEqual(ai.Meter(box.get, box.__setitem__, 0).check(), None)  # 0 = no limit

    def test_reader_stops_at_limit(self):
        calls = []
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: calls.append(kw)))
        box = {"ai_spend:2026-09-30": "5.00"}
        meter = ai.Meter(box.get, box.__setitem__, 5.00, today=lambda: date(2026, 9, 30))
        reader = ai.ClaudeReader("test", client=client, playbook="", meter=meter)
        with self.assertRaises(ai.AIError):
            reader.draft("reply", {"summary": "x"})
        self.assertEqual(calls, [])  # nothing was sent


if __name__ == "__main__":
    unittest.main()
