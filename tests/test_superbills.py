"""Superbill workflow tests. Every name, number and date here is made up."""
import os
import tempfile
import unittest
from datetime import date, datetime

from recordfinder import rules, schedule, superbills
from recordfinder.index import Store
from recordfinder.parser import parse_requests
from recordfinder.superbills import PatientQuery

GRID = "\n".join(f"{c} Some procedure" for c in (99204, 99205, 99213, 99214, 99215, 92285, 92541, 92542, 92546, 95992,
                                                  95886, 95908, 64405, 20552)) + "\n" + \
       " ".join(f"H{n:02}.{n % 9}" for n in range(10, 30)) + " R51.9 G43.909 R42 H81.10"


def packet_texts():
    return [
        "Thursday September 24 schedule Sample, Alice DOB: 01/02/1990  Doe, John DOB: 03/04/1950",  # cover
        GRID + "\nPATIENT NAME Alce Smple",                                                         # superbill 1
        "BLUE PLAN Member Name ALICE SAMPLE Member ID 999",                                         # her card
        "Referred To\nDana Reedly, MD\nREEDLY MEDICAL INC\nSample, Alice (MRN 1234)  DOB 01/02/1990", # her referral
        GRID,                                                                                       # superbill 2
        "MEDICARE HEALTH INSURANCE Name JOHN DOE",                                                  # his card
    ]


class BatchNameTests(unittest.TestCase):
    def test_names(self):
        cases = {
            "EC 09.24.26.pdf": ("EC", date(2026, 9, 24), ""),
            "TY EMG NCV 09.24.26.pdf": ("TY EMG NCV", date(2026, 9, 24), ""),
            "MP 2 06.01.26 Amended .pdf": ("MP 2", date(2026, 6, 1), "Amended"),
            "EC 09   .03.26.pdf": ("EC", date(2026, 9, 3), ""),
            "HB 01 05 24 Revised.pdf": ("HB", date(2024, 1, 5), "Revised"),
            "JP 05.15.2026.pdf": ("JP", date(2026, 5, 15), ""),
        }
        for name, (code, day, note) in cases.items():
            b = superbills.parse_batch_name(name)
            self.assertEqual((b.code, b.day, b.note), (code, day, note), name)
        self.assertTrue(superbills.parse_batch_name("MP 2 06.01.26 Amended .pdf").amended)
        self.assertIsNone(superbills.parse_batch_name("CDP, VNG.pdf"))
        self.assertIsNone(superbills.parse_batch_name("EC 09.24.26.docx"))

    def test_find_batches(self):
        with tempfile.TemporaryDirectory() as root:
            for rel in ("September 2026/EC 09.24.26.pdf", "September 2026/Uploaded/EC 09.23.26.pdf",
                        "September 2026/TY EMG NCV 09.24.26.pdf", "September 2026/Uploaded/TY 09.24.26.pdf",
                        "2026/August 2026/TY 08.14.26.pdf", "October 2026/CC 09.30.26.pdf"):
                p = os.path.join(root, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "w").close()
            self.assertEqual([b.name for b in superbills.find_batches(root, date(2026, 9, 24), "TY")],
                             ["TY 09.24.26.pdf", "TY EMG NCV 09.24.26.pdf"])
            self.assertEqual(len(superbills.find_batches(root, date(2026, 9, 24))), 3)
            self.assertEqual([b.name for b in superbills.find_batches(root, date(2026, 8, 14), "TY")], ["TY 08.14.26.pdf"])
            self.assertEqual([b.name for b in superbills.find_batches(root, date(2026, 9, 30), "CC")], ["CC 09.30.26.pdf"])


class PacketTests(unittest.TestCase):
    def test_split_and_match(self):
        texts = packet_texts()
        cover, packets = superbills.split_packets(texts)
        self.assertEqual(cover, [0])
        self.assertEqual([(p.superbill, p.pages) for p in packets], [(1, [1, 2, 3]), (4, [4, 5])])
        b = superbills.Batch("x/AB 09.24.26.pdf", "AB", date(2026, 9, 24), "")
        best = superbills.best_matches(b, texts, PatientQuery("Alice", "Sample", "", date(1990, 1, 2)), expected_index=0)[0]
        self.assertEqual(best.page, 2)
        self.assertIn("DOB matches", best.reasons)
        doe = superbills.best_matches(b, texts, PatientQuery("John", "Doe"))[0]
        self.assertEqual(doe.page, 5)
        self.assertFalse(best.weak)
        self.assertEqual(superbills.referral_physicians(texts, best.packet), ["Dana Reedly"])
        # Nothing printed identifies this patient: only the schedule position points anywhere,
        # so both packets come back as candidates and the match is flagged for a person to check.
        only_pos = superbills.best_matches(b, texts, PatientQuery("Zed", "Nobody"), expected_index=1)
        self.assertEqual([m.page for m in only_pos], [5, 2])
        self.assertTrue(all(m.weak for m in only_pos))

    def test_ocr_confusions(self):
        self.assertTrue(superbills.name_in("Alex Homer", "Horner"))        # rn read as m
        self.assertFalse(superbills.name_in("Sampleton, Rick", "Sample"))  # different surname


class ScheduleTests(unittest.TestCase):
    SUBJECTS = [
        "#AB Copay $10 Sample, Alice 'Ally' DOB: 01/02/1990 Ph# 555-000-1111  Ins: SCMG ID: 000111222 a@example.com CC: Dizziness Ref: Pat Primary, MD Fax: 555*xx*Conf w/AB*yy",
        "*Doe Jr, John DOB: 03/04/1950 Ph: 555 Ins: MCR ID: 1AA1AA1AA11 Sec: Blue Plan ID: ZZZ Cc: Ear noise Ref: none",
        "#AB COPAY $20 Example, Kim 123456 DOB: 04/22/2003 Ph: 555 Ins: UHC ID: 1 Cc: Seizure Ref: N/A",
        "#Fiction, Sam DOB: 09/30/1956 Ph: 555 Medicare Ins: 2AA0AA1AA05 Anthem: XYZ Cc: Stenosis",
        "Lunch w/ Rep Larson, Stephanie 555-0000",
    ]

    def test_parse_subject(self):
        a = schedule.parse_subject(self.SUBJECTS[0])
        self.assertEqual((a["last"], a["first"], a["nickname"], a["dob"], a["insurance"], a["copay"]),
                         ("Sample", "Alice", "Ally", date(1990, 1, 2), "SCMG", "10"))
        b = schedule.parse_subject(self.SUBJECTS[1])
        self.assertEqual((b["last"], b["suffix"], b["first"], b["insurance"], b["secondary"]), ("Doe", "Jr", "John", "MCR", "Blue Plan"))
        c = schedule.parse_subject(self.SUBJECTS[2])
        self.assertEqual((c["last"], c["account"]), ("Example", "123456"))
        d = schedule.parse_subject("INFUSION Mock, Rob DOB: 12/16/1945 Ins: MCR")
        self.assertEqual(d["last"], "Mock")
        self.assertEqual(schedule.parse_subject("ONLY KD Sample, Yan DOB: 02/08/1963")["last"], "Sample")
        self.assertEqual(schedule.parse_subject("De La Sample, Rodolfo DOB: 02/04/1944")["last"], "De La Sample")
        e = schedule.parse_subject("Placeholder, Barb Acc # 12345 09/28/1937 Ph:555-000-1111 Ins: MCR")  # no "DOB" label
        self.assertEqual((e["last"], e["account"], e["dob"]), ("Placeholder", "12345", date(1937, 9, 28)))

    def test_appointments(self):
        day = datetime(2026, 9, 24)
        mk = lambda h, m, mins, subj, cats=(): schedule.Appointment("Alex Brown", day.replace(hour=h, minute=m),
                                                                    day.replace(hour=h + (m + mins) // 60, minute=(m + mins) % 60), subj, list(cats))
        appts = [mk(10, 0, 30, self.SUBJECTS[0], ["HMO / AUTH", "Follow-Up"]), mk(10, 0, 60, self.SUBJECTS[1], ["New Patient"]),
                 mk(9, 0, 30, self.SUBJECTS[2], ["No Show"]), mk(11, 0, 60, self.SUBJECTS[3]), mk(12, 0, 60, self.SUBJECTS[4])]
        order = schedule.schedule_order(appts, "Alex Brown")
        self.assertEqual([a.info["last"] for a in order], ["Doe", "Sample", "Fiction"])  # no-show and rep lunch left out
        self.assertTrue(appts[1].medicare)
        self.assertTrue(appts[3].medicare)       # "Medicare" typed before "Ins:"
        self.assertFalse(appts[0].medicare)
        hit = schedule.find_patient(appts, PatientQuery("Ally", "Sample"))[0]
        self.assertIs(hit[1], appts[0])
        self.assertEqual(schedule.summarize(appts, "Alex Brown").patients, 3)


ROSTER = {"roster": [
    {"code": "DR", "name": "Dana Reedly, MD", "kind": "physician", "calendar": "DR", "column": "DR", "side": "left", "aliases": ["Dr. Reedly"]},
    {"code": "SL", "name": "Sam Leeson, MD", "kind": "physician", "calendar": "Dr. Leeson", "column": "SL", "side": "right",
     "aliases": ["Dr. Leeson"], "codes": ["SL 2"]},
    {"code": "AB", "name": "Alex Brown, PA-C", "kind": "pa", "calendar": "Alex Brown"},
]}
RULES = {"default": "DR", "payer_excludes": {"SCMG": ["SL"]}, "present_categories": {"DR has to be present": "DR"}}


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.roster = rules.roster_from(ROSTER)
        self.pa = rules.by_code(self.roster, "AB")

    def test_lookup(self):
        self.assertEqual(rules.by_code(self.roster, "SL2").name, "Sam Leeson, MD")
        self.assertEqual(rules.by_code(self.roster, "AB EMG NCV").code, "AB")
        self.assertEqual(rules.by_calendar(self.roster, "dr. leeson").code, "SL")

    def test_payer_exclusion(self):
        d = rules.decide(self.pa, self.roster, RULES, payer="SCMG")
        self.assertEqual((d.attending.code, d.confidence), ("DR", "high"))

    def test_present_tag_wins(self):
        d = rules.decide(self.pa, self.roster, RULES, categories=["DR has to be present", "Follow-Up"])
        self.assertEqual(d.attending.code, "DR")

    def test_out_note_and_referral(self):
        d = rules.decide(self.pa, self.roster, RULES, day_notes=["Dr. Reedly OUT all day", "WM out 12 latest"])
        self.assertEqual(d.attending.code, "SL")
        d = rules.decide(self.pa, self.roster, RULES, referral_names=["Sam Leeson"])
        self.assertEqual(d.attending.code, "SL")

    def test_busy_then_default(self):
        d = rules.decide(self.pa, self.roster, RULES, physician_patients={"Dr. Leeson": 4})
        self.assertEqual((d.attending.code, d.confidence), ("SL", "medium"))
        d = rules.decide(self.pa, self.roster, RULES)
        self.assertEqual((d.attending.code, d.confidence), ("DR", "low"))

    def test_physician_visit_needs_no_attending(self):
        d = rules.decide(rules.by_code(self.roster, "DR"), self.roster, RULES)
        self.assertIsNone(d.attending)


class ParserAndStoreTests(unittest.TestCase):
    def test_provider_not_circled(self):
        r = parse_requests("ACCOUNT 123456 PT Alice B Sample DOS 09/24/2026 AB. PROVIDER NOT CIRCLED")[0]
        self.assertEqual(r.kind, "Provider not circled")
        self.assertEqual(r.patient.visit_code, "AB")
        r = parse_requests("ACCOUNT 234567 PT Bob C Tester Jr DOS 09/22/2026 AB. NO PROVIDED CIRCLED AND YES NO NOT CIRCLED")[0]
        self.assertEqual(r.kind, "Provider not circled")
        self.assertEqual((r.patient.first, r.patient.last, r.patient.display_name), ("Bob", "Tester", "Bob C Tester Jr"))
        r = parse_requests("ACCOUNT 90123 PT Jo K Placeholder DOS 09/21/2026 MP2. 64405 WAS CIRCLED BUT BOTH WERE CIRCLED")[0]
        self.assertEqual(r.kind, "Coding question")

    def test_ocr_cache_and_amendments(self):
        from recordfinder.ocr import Word
        with tempfile.TemporaryDirectory() as root:
            store = Store(os.path.join(root, "index.db"))
            try:
                store.put_ocr("a.pdf", 10, 1.0, 0, [[Word("HELLO", 1, 2, 3, 4)]])
                self.assertEqual(store.get_ocr("a.pdf", 10, 1.0)[0][0][0].text, "HELLO")
                self.assertEqual(store.get_ocr("a.pdf", 11, 1.0), {})   # file changed -> cache dropped
                store.add_amendment({"account": "0123456", "dos": "2026-09-24", "provider": "P", "files": ["x.pdf"], "initials": "AB"})
                self.assertEqual(store.amendments_for("123456", "2026-09-24")[0]["files"], ["x.pdf"])
            finally:
                store.close()


try:
    import pymupdf
except ImportError:  # pragma: no cover
    pymupdf = None


@unittest.skipIf(pymupdf is None, "PyMuPDF not installed")
class AmendTests(unittest.TestCase):
    def test_marks_and_stamp(self):
        from recordfinder import amend
        from recordfinder.ocr import Word
        W = [Word("DR", 432, 600, 440, 606), Word("PROVIDER", 490, 600, 520, 606), Word("SL", 580, 600, 586, 606),
             Word("DANA", 483, 611, 492, 617), Word("REEDLY", 494, 611, 514, 617),
             Word("SAM", 486, 621, 495, 627), Word("LEESON", 497, 621, 518, 627),
             Word("ALEX", 484, 632, 494, 638), Word("BROWN,", 497, 632, 515, 638), Word("PA-C", 518, 632, 529, 638),
             Word("YES", 431, 682, 438, 688), Word("MEDICARE", 477, 682, 500, 688), Word("NO", 580, 682, 586, 688)]
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "AB 09.24.26.pdf")
            doc = pymupdf.open()
            doc.new_page(width=610, height=787)
            doc.new_page(width=610, height=787)
            doc.save(src)
            doc.close()
            out = os.path.join(tmp, "out.pdf")
            st = amend.Stamp(initials="AB", provider="Alex Brown, PA-C", attending="Dana Reedly, MD (DR)",
                             evidence="Outlook sched 9/24 10:00 (Alex Brown)", account="123456", dos="09/24/2026")
            marks = amend.amend_page(src, 1, out, provider_surname="Brown", roster_surnames=["Reedly", "Leeson", "Brown"],
                                     attending_column="DR", attending_side="left", incident_to="YES", stamp=st, words=W)
            self.assertAlmostEqual(marks["attending_x"], 436, delta=2)
            self.assertAlmostEqual(marks["provider_row"][1], 632, delta=1)
            self.assertEqual(marks["incident_to"], "YES")
            self.assertGreater(marks["stamp"][1], 688)
            self.assertTrue(amend.is_our_amendment(out))
            self.assertFalse(amend.is_our_amendment(src))
            with pymupdf.open(out) as d:
                self.assertEqual(len(d), 1)
                self.assertIn("AMENDED", d[0].get_text())
                self.assertIn("*AB", d[0].get_text())

    def test_outline_row_keeps_neighbouring_codes_out(self):
        from recordfinder import amend
        from recordfinder.ocr import Word
        # "H92.09 | [pen-circled G56.01, unreadable] | Right Carpal Tunnel Syndrome Left | GS6.02.- (OCR'd G56.02)"
        W = [Word("H92.09", 177, 500, 193, 506), Word("Right", 243, 500, 256, 506), Word("Carpal", 278, 500, 291, 506),
             Word("Tunnel", 293, 500, 307, 506), Word("Syndrome", 310, 500, 330, 506), Word("Left", 346, 500, 354, 506),
             Word("GS6.02.-", 389, 500, 410, 506), Word("R20.2", 389, 515, 402, 521), Word("M54.6", 388, 470, 402, 476),
             Word("Carpal", 300, 120, 313, 126), Word("Tunnel", 315, 120, 329, 126)]   # same words higher up the form
        doc = pymupdf.open()
        page = doc.new_page(width=610, height=787)
        rect = amend.outline_row(page, W, ["Carpal", "Tunnel"], below=0.3)
        self.assertGreater(rect[0], 193)       # H92.09 (next column) stays outside
        self.assertLess(rect[0], 243 - 30)     # but the circled G56.01 cell is inside
        self.assertGreaterEqual(rect[2], 410)  # G56.02 fully inside
        self.assertGreater(rect[1], 400)       # the lower row, not the one at the top
        self.assertTrue(amend.codeish("GS6.02.-") and amend.codeish("H54S") and not amend.codeish("Carpal"))

    def test_names(self):
        from recordfinder import amend
        self.assertEqual(amend.billing_name("AB", date(2026, 9, 24), "SAMPLE", "alice"), "Sample Alice AB 09.24.26 SB.pdf")
        self.assertEqual(amend.billing_folder("S:\\X\\{yyyy}\\{Month}\\Missing", date(2026, 9, 24)), "S:\\X\\2026\\September\\Missing")

    # The office's superbill as OCR reads it: header boxes at the top, the PROVIDER box bottom right with its
    # six rows ~10.3 pt apart, then the MEDICARE / TRICARE rows. Made-up names, the template's geometry.
    ROSTER = [("DANA", "REEDLY,", "MD"), ("SAM", "LEESON,", "MD"), ("ALEX", "BROWN,", "PA-C"),
              ("JO", "MARSH,", "PA-C"), ("KIM", "NOLAN,", "PA-C"), ("RAY", "OKAFOR,", "PA-C")]
    ROW_Y = [613.0, 623.3, 634.3, 644.6, 655.0, 665.3]

    def form_words(self, unread=(), dx=0.0, dy=0.0, below=(), names=None):
        from recordfinder.ocr import Word
        W = [Word("DATE", 349.8, 43.0, 377.0, 53.4), Word("OF", 379.3, 43.0, 392.6, 53.4), Word("BIRTH", 394.9, 43.0, 427.2, 53.4),
             Word("DATE", 477.8, 43.0, 505.0, 53.4), Word("OF", 507.3, 43.0, 520.6, 53.4), Word("SERVICE", 522.9, 43.0, 566.4, 53.4),
             Word("IMP", 431.7, 602.8, 438.9, 611.0), Word("PROVIDER", 498.0, 602.9, 520.7, 612.2), Word("MP", 578.9, 602.8, 584.6, 611.0)]
        for i, (names, y) in enumerate(zip(names or self.ROSTER, self.ROW_Y)):
            if i in unread:          # the provider's pen stroke runs through the row: OCR reads nothing there
                continue
            x = 509.5 - sum(len(n) * 4.4 + 2.6 for n in names) / 2      # centred in the box, like the form
            for n in names:
                W.append(Word(n, x, y, x + len(n) * 4.4, y + 9.3))
                x += len(n) * 4.4 + 2.6
        W += [Word("YES", 430.0, 685.4, 436.8, 693.6), Word("MEDICARE", 476.4, 685.5, 499.1, 694.8),
              Word("INCIDENT-TO", 502.0, 685.5, 530.9, 694.8), Word("MET", 533.5, 685.5, 542.5, 694.8), Word("NO", 578.9, 685.4, 585.0, 693.6),
              Word("YES", 430.0, 695.7, 436.8, 703.9), Word("TRICARE", 474.4, 695.8, 492.9, 705.1),
              Word("COLLABORATIVE", 495.8, 695.8, 531.3, 705.1), Word("VISIT", 533.9, 695.8, 544.5, 705.1), Word("NO", 578.9, 695.7, 585.0, 703.9),
              Word("G47.33", 386.8, 705.9, 403.1, 715.2), Word("G25.81", 386.8, 715.9, 403.1, 725.3), *below]
        return [Word(w.text, w.x0 + dx, w.y0 + dy, w.x1 + dx, w.y1 + dy) for w in W]

    def amend_form(self, words, provider="Brown", roster=None, **kw):
        from recordfinder import amend
        roster = roster or [r[1].strip(",").title() for r in self.ROSTER]
        stamp = kw.pop("stamp", None) or amend.Stamp(initials="AB", provider="Alex Brown, PA-C", attending="Dana Reedly, MD (IMP)",
                                                     evidence="Outlook sched 9/24 10:00 (Alex Brown)", account="123456", dos="09/24/2026")
        with tempfile.TemporaryDirectory() as tmp:
            src, out = os.path.join(tmp, "AB 09.24.26.pdf"), os.path.join(tmp, "out.pdf")
            doc = pymupdf.open()
            doc.new_page(width=612, height=792)
            doc.save(src)
            doc.close()
            marks = amend.amend_page(src, 0, out, provider_surname=provider, roster_surnames=roster, stamp=stamp,
                                     words=words, **kw)
            with pymupdf.open(out) as d:
                return marks, d[0].get_text()

    def test_long_stamp_line_wraps_under_the_provider_box(self):
        from recordfinder import amend
        # The "Per ..." line is wider than the space under the box (~172 pt) at every size, so it wraps; before
        # the fix the height was guessed from the line count, nothing fitted, and the stamp went over DOB / DOS.
        st = amend.Stamp(initials="AB", provider="Alex Brown, PA-C", attending="Dana Reedly, MD (IMP)",
                         evidence="Outlook sched 9/24 10:00 (Alex Brown) and the signed progress note",
                         account="123456", dos="09/24/2026", extra=["Provider not circled on original; per billing (MPS) request"])
        marks, text = self.amend_form(self.form_words(), attending_column="IMP", attending_side="left", incident_to="YES", stamp=st)
        x0, y0, x1, y1 = marks["stamp"]
        self.assertGreater(y0, 705.1)                    # under the TRICARE row, not over the DOB / DOS boxes
        self.assertGreaterEqual(y0, amend.TOP_CLEAR)
        self.assertLessEqual(y1, 792 - 3)
        self.assertGreater(x0, 403.1)                    # the ICD code column to the left stays clear
        flat = " ".join(text.split())
        for part in ("AMENDED", "*AB", "signed progress note", "per billing (MPS) request", "Processed via DizzyDoctor"):
            self.assertIn(part, flat)                    # nothing cut off

    def test_stamp_with_no_room_raises_instead_of_covering_the_header(self):
        from recordfinder import amend
        from recordfinder.ocr import Word
        handwriting = [Word("note", 450, 762, 480, 768)]   # something written low in the box: only ~15 pt left
        with self.assertRaises(amend.LayoutError):
            self.amend_form(self.form_words(below=handwriting), attending_column="IMP", attending_side="left")

    def test_stamp_box_is_measured_not_estimated(self):
        from recordfinder import amend
        text = "AMENDED\n" + "word " * 60 + "\nProcessed via DizzyDoctor"     # one very long line
        box, size = amend.stamp_box(612, 792, 410, 711, text)
        self.assertGreaterEqual(box.y0, 711)
        self.assertLessEqual(box.y1, 789)
        probe = pymupdf.open().new_page(width=612, height=792)
        self.assertGreaterEqual(probe.insert_textbox(box + amend.STAMP_PAD, text, fontsize=size, fontname="helv"), 0)
        with self.assertRaises(amend.LayoutError):
            amend.stamp_box(612, 792, 410, 30, "line\n" * 120)              # can't fit: never falls back to the top

    def test_unread_provider_row_is_inferred_between_its_neighbours(self):
        for dx, dy in ((0, 0), (3.2, 0.5)):              # the scan sits a little off the template
            marks, _ = self.amend_form(self.form_words(unread={2}, dx=dx, dy=dy),
                                       attending_column="IMP", attending_side="left", incident_to="YES")
            row = marks["provider_row"]
            self.assertTrue(marks["provider_row_inferred"])
            self.assertAlmostEqual(row[1], self.ROW_Y[2] + dy, delta=1.0)
            self.assertGreater(row[1], self.ROW_Y[1] + dy + 5)
            self.assertLess(row[3], self.ROW_Y[3] + dy + 2)
            self.assertAlmostEqual((row[0] + row[2]) / 2, 509.5 + dx, delta=3)
            self.assertAlmostEqual(marks["attending_x"], 435.3 + dx, delta=2)
            self.assertGreater(marks["stamp"][1], 705.1 + dy)

    def test_unread_first_or_last_row_is_inferred(self):
        from recordfinder import amend
        for k, name in ((0, "Reedly"), (5, "Okafor")):
            lay = amend.locate(self.form_words(unread={k}), 612, 792, name,
                               [r[1].strip(",").title() for r in self.ROSTER])
            self.assertTrue(lay["inferred"])
            self.assertAlmostEqual(lay["row"].y0, self.ROW_Y[k], delta=1.0)
            if k == 0:
                self.assertAlmostEqual(lay["headers"]["IMP"], 435.3, delta=1)   # headers still found above the list

    # Short names around a wider one: the unread first row's name is wider than the rows next to it.
    WIDE = [("MARGARET", "LUNA,", "MD"), ("AL", "DIAZ,", "MD"), ("BO", "CRUZ,", "PA-C"),
            ("MARIANNE", "BROWN,", "PA-C"), ("ED", "ROSS,", "PA-C"), ("JO", "NOLAN,", "PA-C")]

    @staticmethod
    def printed(names):
        total = sum(len(n) * 4.4 + 2.6 for n in names)
        return 509.5 - total / 2, 509.5 + total / 2 - 2.6

    def test_inferred_row_is_as_wide_as_the_list_not_its_neighbours(self):
        from recordfinder import amend
        roster = [r[1].strip(",").title() for r in self.WIDE]
        lay = amend.locate(self.form_words(unread={0}, names=self.WIDE), 612, 792, "Luna", roster)
        x0, x1 = self.printed(self.WIDE[0])
        self.assertTrue(lay["inferred"])
        self.assertLessEqual(lay["row"].x0 - 9, x0)    # the oval (row +/- 9) goes round the whole name
        self.assertGreaterEqual(lay["row"].x1 + 9, x1)
        self.assertGreater(lay["row"].x0, lay["col_x"]["left"] + 20)   # but not the whole column: LUNA isn't long

    def test_inferred_row_with_a_longer_surname_is_widened(self):
        from recordfinder import amend
        names = [("AL", "LUNA,", "MD"), ("AL", "DIAZ,", "MD"), ("BO", "CRUZ,", "PA-C"), ("ED", "ROSS,", "PA-C"),
                 ("JO", "KANE,", "PA-C"), ("JOSEPH", "WASHINGTON,", "PA-C")]   # far wider than every row OCR read
        roster = [r[1].strip(",").title() for r in names]
        lay = amend.locate(self.form_words(unread={5}, names=names), 612, 792, "Washington", roster)
        x0, x1 = self.printed(names[5])
        self.assertLessEqual(lay["row"].x0 - 9, x0)
        self.assertGreaterEqual(lay["row"].x1 + 9, x1)
        self.assertGreater(lay["row"].x0 - 9, lay["col_x"]["left"] + 5.2)    # clear of the circled X columns
        self.assertLess(lay["row"].x1 + 9, lay["col_x"]["right"] - 5.2)

    def test_read_provider_row_is_not_inferred(self):
        marks, _ = self.amend_form(self.form_words(), attending_column="IMP", attending_side="left")
        self.assertFalse(marks["provider_row_inferred"])
        self.assertAlmostEqual(marks["provider_row"][1], self.ROW_Y[2], delta=0.1)

    def test_row_is_not_guessed_when_the_roster_is_out_of_printed_order(self):
        from recordfinder import amend
        shuffled = ["Leeson", "Reedly", "Brown", "Marsh", "Nolan", "Okafor"]
        with self.assertRaises(amend.LayoutError):
            amend.locate(self.form_words(unread={2}), 612, 792, "Brown", shuffled)
        with self.assertRaises(amend.LayoutError):      # not on the roster at all
            amend.locate(self.form_words(unread={2}), 612, 792, "Brown", ["Reedly", "Leeson", "Marsh", "Nolan", "Okafor"])
        with self.assertRaises(amend.LayoutError):      # only one other row read: no pitch to go by
            amend.locate(self.form_words(unread={0, 1, 2, 3, 4}), 612, 792, "Brown",
                         [r[1].strip(",").title() for r in self.ROSTER])


if __name__ == "__main__":
    unittest.main()
