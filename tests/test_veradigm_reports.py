"""Veradigm Revenue Detail import, open balances and the name cross-check, on a made-up report PDF."""
import os
import tempfile
import unittest
from datetime import date

try:
    import pymupdf
except ImportError:  # pragma: no cover
    pymupdf = None

from recordfinder import people, superbill_map, veradigm_reports as vr
from recordfinder.index import Store
from recordfinder.rules import Provider

# (service date, voucher, pt #, name, units, rvus, charges, payments, refunds, adjusts, net)
ROWS = [
    ("09/01/2026", "51366845", "277670", "Alice M Sample", "6.00", "11.21", "854.00", "490.50", "0.00", "363.50", "0.00"),
    ("09/02/2026", "51366846", "277670", "Alice M Sample", "2.00", "7.37", "410.00", "241.65", "0.00", "106.70", "61.65"),
    ("05/02/2026", "51366847", "286160", "Bob Samples", "2.00", "6.35", "400.00", "0.00", "0.00", "100.00", "300.00"),
    ("09/03/2026", "51366848", "286160", "Bob Samples", "1.00", "4.60", "275.00", "315.00", "0.00", "0.00", "-40.00"),
    ("09/04/2026", "51366849", "11110", "Carl De La Sample", "1.00", "4.60", "275.00", "0.00", "0.00", "0.00", "275.00"),
]
XS = [18, 72, 132, 163, 302, 339, 377, 423, 479, 516, 563]


def make_report(path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((18, 30), "9/30/2026 11:08:05 AM (PST)   Revenue Detail Report", fontsize=8)
    page.insert_text((201, 44), "Service Dates: 09/01/2026 - 09/30/2026", fontsize=8)
    for x, w in zip((18, 72, 132, 297, 334, 369, 408, 461, 511, 573),
                    ("Service", "Voucher", "Patient", "Units", "RVUs", "Charges", "Payments", "Refunds", "Adjusts", "Net")):
        page.insert_text((x, 60), w, fontsize=7)
    y = 80
    for r in ROWS:
        cells = list(r[:3]) + [r[3]] + list(r[4:])
        for x, v in zip(XS, cells):
            page.insert_text((x, y), v, fontsize=7)
        y += 12
    page.insert_text((18, y + 20), "Grand Total 1 2 3", fontsize=7)
    doc.save(path)
    doc.close()


@unittest.skipIf(pymupdf is None, "pymupdf not installed")
class RevenueDetailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "index.db"))
        self.all = os.path.join(self.tmp.name, "ALL")
        os.makedirs(self.all)
        self.pdf = os.path.join(self.all, "Revenue Detail by Voucher 09.01.26-09.30.26 All Patients.pdf")
        make_report(self.pdf)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_parse(self):
        vs, info = vr.parse_revenue_detail(self.pdf)
        self.assertEqual(info, {"service_from": "2026-09-01", "service_to": "2026-09-30", "run_date": "2026-09-30"})
        self.assertEqual([v.voucher for v in vs], [r[1] for r in ROWS])
        self.assertEqual(vs[4].name, "Carl De La Sample")
        self.assertEqual((vs[1].charges, vs[1].payments, vs[1].adjusts, vs[1].net), (410.0, 241.65, 106.7, 61.65))
        self.assertEqual(vs[3].net, -40.0)
        self.assertTrue(vr.is_revenue_detail(self.pdf))

    def test_import_open_and_aging(self):
        self.assertEqual(vr.import_folder(self.store, self.all), 5)
        self.assertEqual(vr.import_folder(self.store, self.all), 0)                     # unchanged: not read again
        ob, open_vs = vr.open_for(self.store, "277670")
        self.assertEqual((ob.total, ob.vouchers, ob.as_of), (61.65, 1, date(2026, 9, 30)))
        self.assertEqual([v.voucher for v in open_vs], ["51366846"])                      # the paid visit isn't open
        bob, _ = vr.open_for(self.store, "286160")
        self.assertEqual(bob.total, 260.0)                                                 # 300 open + a 40 credit
        self.assertEqual(bob.aging, [-40.0, 0.0, 0.0, 300.0])                              # May visit is over 90 days
        totals = vr.open_totals(self.store)
        self.assertEqual(sorted(totals), ["11110", "277670", "286160"])
        self.assertEqual(vr.status(self.store), (5, 3, "2026-09-30"))

    def test_cross_check(self):
        vr.import_report(self.store, self.pdf)
        people.ensure_schema(self.store)
        with self.store._lock:
            self.store.db.executemany("INSERT INTO patients VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
                (1, "Sample", "Alice", "SAMPLE", "ALICE", "1950-01-02", "277670", "", "", 1, 0),      # same person
                (2, "Sampson", "Robert", "SAMPSON", "ROBERT", "1960-03-04", "286160", "", "", 1, 0),  # different name
            ])
            self.store.db.commit()
        got = {c.acct: c for c in vr.cross_check(self.store)}
        self.assertNotIn("277670", got)                         # middle initial doesn't matter
        self.assertEqual(got["286160"].issue, "different name")
        self.assertEqual(got["286160"].outlook, "Sampson, Robert")
        self.assertEqual(got["11110"].issue, "Pt # not in any Outlook header")


class SuperbillCalendarTests(unittest.TestCase):
    def test_calendar_for(self):
        roster = [Provider("TY", "Dr. Yari", "PA", "Touraj", "", [], []),
                  Provider("EC", "Evan Camarillo", "PA", "Evan Camarillo", "", [], [])]
        self.assertEqual(superbill_map.calendar_for("TY", roster), "Touraj")
        self.assertEqual(superbill_map.calendar_for("TY EMG NCV", roster), "TY EMG/NCV")   # the EMG batches
        self.assertEqual(superbill_map.calendar_for("EJ", roster), "Emily Jing")           # not in the roster
        self.assertEqual(superbill_map.calendar_for("EC 2", roster), "Evan Camarillo")     # a second batch that day
        self.assertIsNone(superbill_map.calendar_for("ZZ", roster))


if __name__ == "__main__":
    unittest.main()
