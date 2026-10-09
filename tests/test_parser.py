"""All names, account numbers and dates below are made up; they only mimic the chat's formats."""
import os
import tempfile
import time
import unittest
from datetime import date

from recordfinder.index import Store
from recordfinder.matcher import rank, search_terms
from recordfinder.parser import parse_requests

TODAY = date(2026, 9, 25)  # a Friday

CHAT = """Message List
please post payment $12.34 Payment Sep 18,... by Front Desk
Friday 12:21 PM
Front Desk
please post payment $12.34 Payment
Sep 18, 2026 12:20 pm
Collected at: Example Clinic, M.D, APC
Note: 09/18/26 -Doe, Jane 111222 $12.34 Co-Insurance DOS:06/03/26, 07/01/26 * FD
Receipt #abcd

Square: Sign in to Your Dashboard & Manage your Business
app.squareup.com

1 Checkmark reaction.
Pls provide M/R for Pt: Alice B Sample... by Billing One
Billing One
Tuesday 8:17 AM
Pls provide M/R for Pt: Alice B Sample Acct#:123456. Aetna is downcoding and underpaying from 99205 to 99204 for DOS 9/3/2026

1 Like reaction.
PLEASE PROVIDE M/R TO APPEAL FOR ACCOUNT 23... by Billing Two
Billing Two
Monday 9:34 AM
PLEASE PROVIDE M/R TO APPEAL FOR ACCOUNT 234567 PT BOB C TESTER DOS 09/01/2026. AETNA IS DOWN CODING FROM 99215 TO 99213.

1 Like reaction.
  ACCOUNT 345678 PT Carl D Example DOS 09... by Billing Two
Billing Two
Wednesday 10:35 AM
 ACCOUNT 345678 PT Carl D Example DOS 09/08/2026 MISSING AUTH FOR 92550 AND 92548
ACCOUNT 456789 PT Dana E Fake DOS 09/14/2026 MISSING AUTH FOR 92548

NOT ON SCMG PORTAL
Front Desk  Requested medica record... by Billing Three
Billing Three
Wednesday 11:45 AM
Front Desk
 Requested medica records
567890 -Erin F Mock - DOB 06/16/1993 - DOS 08/13/2026
678901 -Frank Van Der Sample - DOB 06/03/1961 - DOS 09/03/2026
567890 -Erin F Mock - DOB 06/16/1993 - DOS 09/08/2026
Front Desk  WE HAVE BAD ADDRESS FOR... by Billing Four
Billing Four
Monday 3:11 PM
Front Desk
 WE HAVE BAD ADDRESS FOR PATIENT ACCOUNT 789012 Gail H Nobody WE NEED CORRECT ADDRESS 123 MAIN ST
Santee, CA 92071 PATIENT HAS BALANCE OF $255.14
WAS PT HANK TESTCASE DOB 08/04/1970 SEEN ON 0... by Billing Two
Billing Two
Wednesday 9:50 AM
WAS PT HANK TESTCASE DOB 08/04/1970 SEEN ON 09/18/2026 IP? MISSING S/B
Front Desk  ACCOUNT 890123 PT Ivy... by Billing Two
Billing Two
Monday 1:23 PM
Front Desk
 ACCOUNT 890123 PT Ivy J Madeup DOS 09/17/2026 TY. ARE WE BILLING PT INS? OR IS PT CASHPAY?
Begin quote, Billing Two, 9/21/2026 1:23... by Front Desk
Monday 10:15 PM
Front Desk
Billing Two9/21/2026 1:23 PM
Front Desk ACCOUNT 890123 PT Ivy J Madeup DOS 09/17/2026 TY. ARE WE BILLING PT INS? OR IS PT CASHPAY?
Both, We collected
ACCOUNT 90123 PT Jo K Placeholder DOS 09/21... by Billing Two
Billing Two
Wednesday 2:43 PM
ACCOUNT 90123 PT Jo K Placeholder DOS 09/21/2026 MP2. 64405 WAS CIRCLED BUT BOTH LEFT AND RIGHT WERE CIRCLED. DO WE BILL FOR 64405 OR 64405-50
Synthetic, Kim 01303 DOB: 02/23/1947 by Front Desk
Yesterday 8:31 AM
Front Desk
Synthetic, Kim 01303 DOB: 02/23/1947
Gracias by Front Desk
Friday 1:21 PM
Front Desk
Gracias
"""


class ParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reqs = parse_requests(CHAT, today=TODAY)
        cls.by_acct = {r.patient.account: r for r in cls.reqs if r.patient and r.patient.account}

    def test_every_patient_found_once(self):
        self.assertEqual(sorted(self.by_acct), sorted(["111222", "123456", "234567", "345678", "456789", "567890",
                                                        "678901", "789012", "890123", "90123", "01303"]))
        self.assertNotIn("92071", self.by_acct)  # zip code, not an account

    def test_names(self):
        self.assertEqual(self.by_acct["123456"].patient.display_name, "Alice B Sample")
        self.assertEqual(self.by_acct["234567"].patient.last, "Tester")
        self.assertEqual(self.by_acct["111222"].patient.first, "Jane")
        self.assertEqual(self.by_acct["789012"].patient.display_name, "Gail H Nobody")
        self.assertEqual(self.by_acct["678901"].patient.last, "Sample")
        kaskos = [r for r in self.reqs if r.patient and r.patient.last == "Testcase"]
        self.assertEqual(len(kaskos), 1)
        self.assertEqual(kaskos[0].patient.dob.value, date(1970, 8, 4))
        self.assertEqual(kaskos[0].patient.dos[0].value, date(2026, 9, 18))

    def test_kinds(self):
        self.assertEqual(self.by_acct["123456"].kind, "Medical records")
        self.assertIn("Appeal / downcode", self.by_acct["234567"].types)
        self.assertEqual(self.by_acct["345678"].kind, "Missing auth")
        self.assertEqual(self.by_acct["789012"].kind, "Address update")
        self.assertEqual(self.by_acct["90123"].kind, "Coding question")
        self.assertEqual(self.by_acct["111222"].kind, "Payment to post")
        self.assertEqual(self.by_acct["01303"].kind, "Status inquiry")

    def test_dates_codes_payers(self):
        self.assertEqual([d.value for d in self.by_acct["111222"].patient.dos], [date(2026, 6, 3), date(2026, 7, 1)])
        self.assertEqual(self.by_acct["234567"].patient.codes, ["99215", "99213"])
        self.assertEqual(self.by_acct["345678"].patient.codes, ["92550", "92548"])
        self.assertEqual(self.by_acct["90123"].patient.codes, ["64405", "64405-50"])
        self.assertEqual(self.by_acct["90123"].patient.visit_code, "MP2")
        self.assertEqual(self.by_acct["123456"].payers, ["Aetna"])
        self.assertIn("SCMG", self.by_acct["456789"].payers)
        mock = self.by_acct["567890"].patient
        self.assertEqual([d.value for d in mock.dos], [date(2026, 8, 13), date(2026, 9, 8)])

    def test_relative_timestamps(self):
        self.assertEqual(self.by_acct["123456"].received.date(), date(2026, 9, 22))  # Tuesday
        self.assertEqual(self.by_acct["01303"].received.date(), date(2026, 9, 24))   # Yesterday

    def test_reply_marks_answered(self):
        self.assertEqual(self.by_acct["890123"].answer, "Both, We collected")
        self.assertEqual(self.by_acct["890123"].answered_by, "Front Desk")

    def test_chitchat_and_noise_ignored(self):
        self.assertFalse(any("Gracias" in r.text for r in self.reqs))
        self.assertFalse(any("squareup" in r.text for r in self.reqs))


class MatcherTests(unittest.TestCase):
    def test_finds_and_ranks_files(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as data:
            files = [
                "Sample, Alice 123456/Progress Note 09-03-2026.pdf",
                "Sample, Alice 123456/Insurance card.jpg",
                "Scans/2026/SAMPLE ALICE 090326.pdf",
                "Scans/2026/Sampleton, Rick 999999.pdf",   # different patient, shares a prefix
                "Scans/2026/Other 1234567.pdf",            # account digits inside a longer number
            ]
            for f in files:
                p = os.path.join(root, f)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "w").close()
            store = Store(os.path.join(data, "index.db"))
            try:
                self._check_ranking(store, root, files)
            finally:
                store.close()  # Windows can't delete an open database

    def _check_ranking(self, store, root, files):
        self.assertEqual(store.rebuild_index([root], []), len(files))
        rows = store.find_files(search_terms("Alice", "Sample", "123456"))
        hits = rank(rows, "Alice", "Sample", "123456", [date(2026, 9, 3)], None, ["note", "progress"])
        names = [h.name for h in hits]
        self.assertEqual(names[0], "Progress Note 09-03-2026.pdf")
        self.assertIn("SAMPLE ALICE 090326.pdf", names)
        self.assertNotIn("Sampleton, Rick 999999.pdf", names)
        self.assertNotIn("Other 1234567.pdf", names)

    def test_item_status_persists(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(os.path.join(root, "index.db"))
            try:
                store.upsert_item("k1", {"text": "x"})
                store.set_status("k1", "Done")
                self.assertEqual(store.upsert_item("k1", {"text": "x"})[0], "Done")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
