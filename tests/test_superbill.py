"""Provider superbill creation, mirroring the progress note flow. Every name and date here is made up."""
import json
import os
import tempfile
import unittest
from datetime import date

from recordfinder.charting import note, superbill


def _note(**over):
    v = note.blank()
    v.update({"Name": "Sample, Alice", "DOB": "01/02/1960", "Date": "09/24/2026", "HPI-age": "66",
              "hpi-female": True, "neuro-consult": False, "neuro-progress": True, "appt-time": "40", "imp-mp": True,
              "Imp/MP": note.FIELDS["Imp/MP"]["options"][0], "next-appt": "4",
              "impression-plan-descr": "1) Dizziness and lack of coordination - Monitor clinically.\n2) Tinnitus AU - Discussed."})
    v.update(over)
    return v


class SuperbillDataTests(unittest.TestCase):
    def test_blank_has_every_field(self):
        sb = superbill.blank()
        for key in ("Name", "DOB", "Date", "acct", "insurance", "rendering", "diagnoses", "procedures"):
            self.assertIn(key, sb)
        self.assertEqual(sb["diagnoses"], [])

    def test_from_note_carries_visit_level_procedures_and_icd(self):
        sb = superbill.from_note(_note(**{"time-proc1": True, "addnl-time": note.FIELDS["addnl-time"]["options"][0],
                                          "goggle-treatment-x": "Right posterior canal BPPV: Epley maneuver performed.",
                                          "imp-rightpost": True}))
        self.assertEqual(sb["Name"], "Sample, Alice")
        self.assertEqual(sb["sex"], "F")
        self.assertEqual(sb["visit"], "established")
        self.assertEqual(sb["minutes"], "40")
        self.assertEqual([(d["code"], d["description"]) for d in sb["diagnoses"]],
                         [("R42", "Dizziness and lack of coordination"), ("H93.13", "Tinnitus AU")])
        cpts = [p["cpt"] for p in sb["procedures"]]
        self.assertEqual(cpts[0], "99215")
        self.assertEqual(sb["procedures"][0]["modifier"], "25")
        self.assertIn("92542", cpts)
        self.assertIn("95992", cpts)

    def test_attach_patient_staples_demos_and_insurance(self):
        chart = {"patient": {"last": "Sample", "first": "Alice", "dob": "01/02/1960", "acct": "123456"},
                 "facts": {"phone": "555-000-1111", "insurance": "SCMG", "secondary": "Medicare",
                           "id1": "000111222", "id2": "1AA1AA1AA11"},
                 "insurance": "SCMG"}
        sb = superbill.attach_patient(superbill.blank(), chart)
        self.assertEqual(sb["Name"], "Sample, Alice")
        self.assertEqual(sb["acct"], "123456")
        self.assertEqual(sb["phone"], "555-000-1111")
        self.assertEqual(sb["insurance"], "SCMG")
        self.assertEqual(sb["insurance_id"], "000111222")
        self.assertEqual(sb["secondary_id"], "1AA1AA1AA11")
        # what the provider typed is kept
        sb = superbill.attach_patient({**superbill.blank(), "insurance": "Aetna"}, chart)
        self.assertEqual(sb["insurance"], "Aetna")

    def test_uploaded_patient_rows_fill_blanks_without_overwriting_clinical_work(self):
        sb = superbill.blank()
        sb["rendering"] = "Provider From Schedule"
        row = {"Last Name": "Sample", "First Name": "Alice", "Date of Birth": "01/02/1990", "MRN": "12345",
               "Primary Insurance": "Aetna", "Member ID": "A-1", "Provider": "Wrong Provider",
               "ICD-10": "R42;H93.13", "Diagnosis": "Dizziness;Tinnitus", "CPT": "99214"}
        filled = superbill.imported(sb, row)
        self.assertEqual(filled["Name"], "Sample, Alice")
        self.assertEqual(filled["acct"], "12345")
        self.assertEqual(filled["rendering"], "Provider From Schedule")
        self.assertEqual([d["code"] for d in filled["diagnoses"]], ["R42", "H93.13"])
        self.assertEqual(filled["procedures"][0]["cpt"], "99214")

    def test_validate_lists_what_is_missing(self):
        self.assertIn("the patient's name", superbill.validate(superbill.blank()))
        sb = superbill.from_note(_note())
        sb["rendering"] = "Alex Brown, PA-C"
        self.assertEqual(superbill.validate(sb), [])


class SuperbillSearchTests(unittest.TestCase):
    def test_icd_search_by_code_and_wording(self):
        self.assertTrue(superbill.search_icd("H81.11"))
        self.assertEqual(superbill.search_icd("H81.11")[0]["code"], "H81.11")
        hits = superbill.search_icd("tinnitus")
        self.assertTrue(all("tinnitus" in (h["description"] + h.get("keywords", "")).lower() for h in hits))
        self.assertEqual(superbill.search_icd(""), [])
        self.assertGreaterEqual(len(superbill.icd_database()), 50)
        for row in superbill.icd_database():
            self.assertTrue({"code", "description"} <= set(row))

    def test_cpt_search(self):
        self.assertEqual(superbill.search_cpt("99215")[0]["code"], "99215")
        self.assertTrue(any(h["code"] == "95992" for h in superbill.search_cpt("epley")))
        self.assertGreaterEqual(len(superbill.cpt_database()), 20)

    def test_the_json_lists_are_valid(self):
        for path in (superbill.ICD_JSON, superbill.CPT_JSON):
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
            self.assertIsInstance(rows, list)
            self.assertGreater(len(rows), 10)


class SuperbillPdfTests(unittest.TestCase):
    def test_fill_and_read_round_trip(self):
        sb = superbill.from_note(_note())
        sb.update({"acct": "123456", "phone": "555-000-1111", "insurance": "SCMG", "insurance_id": "000111222",
                   "rendering": "Alex Brown, PA-C", "attending": "Dana Reedly, MD", "payer": "SCMG"})
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "SampleAlice 09 24 26 SB.pdf")
            superbill.fill(sb, out)
            self.assertTrue(os.path.isfile(out))
            self.assertTrue(os.path.isfile(out + ".json"))
            back = superbill.read(out)
            self.assertEqual(back["Name"], "Sample, Alice")
            self.assertEqual(back["rendering"], "Alex Brown, PA-C")
            self.assertEqual(len(back["diagnoses"]), 2)
            self.assertTrue(back["procedures"])
            import pymupdf
            with pymupdf.open(out) as doc:
                text = "\n".join(p.get_text() for p in doc)
            for want in ("SUPERBILL", "Sample, Alice", "SCMG", "R42", "99215", "Alex Brown"):
                self.assertIn(want, text)

    def test_table_titles_sit_below_the_rows_above_them(self):
        # the "Services (CPT)" title once overlapped the last diagnosis row
        sb = superbill.from_note(_note())
        sb["rendering"] = "Alex Brown, PA-C"
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "SampleAlice 09 24 26 SB.pdf")
            superbill.fill(sb, out)
            import pymupdf
            with pymupdf.open(out) as doc:
                words = doc[0].get_text("words")
        last_dx = max(w[3] for w in words if w[4] in {d["code"] for d in sb["diagnoses"]})   # bottom of the last ICD-10 code
        services_top = min(w[1] for w in words if w[4] == "Services")
        self.assertGreater(services_top, last_dx + 2)

    def test_screen_is_laid_out_like_the_pdf(self):
        # the Superbill tab is the filed sheet, typed into: same title, header lines and table columns
        web = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recordfinder", "charting", "web", "modules")
        with open(os.path.join(web, "superbill.js"), encoding="utf-8") as f:
            js = f.read()
        for want in ('"SUPERBILL"', '"Diagnoses (ICD-10)"', '"Services (CPT)"', '"Rendering: "', '"Primary: "', "Provider-created superbill"):
            self.assertIn(want, js)
        with open(os.path.join(web, "superbill.css"), encoding="utf-8") as f:
            css = f.read()
        self.assertIn("--pt: calc(100cqw / 612)", css)            # sized in the PDF's points
        self.assertIn("min-height: calc(792 * var(--pt))", css)   # a letter-size page

    def test_filename_matches_the_office_convention(self):
        self.assertEqual(superbill.filename("O'Brien", "Mary-Anne", date(2026, 1, 5)), "OBrienMaryAnne 01 05 26 SB.pdf")

    def test_draft_and_final_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"superbill_root": os.path.join(tmp, "Batches")}
            draft = superbill.draft_path(cfg, tmp, "Sample", "Alice", date(2026, 9, 24))
            self.assertTrue(draft.startswith(os.path.join(tmp, "Superbills")))
            final = superbill.final_path(cfg, "Sample", "Alice", date(2026, 9, 24))
            self.assertEqual(final, os.path.join(tmp, "Batches", "2026", "SampleAlice 09 24 26 SB.pdf"))
            sb = superbill.from_note(_note())
            sb["rendering"] = "Alex Brown, PA-C"
            superbill.fill(sb, draft)
            found = superbill.list_for(cfg, tmp, "Sample", "Alice")
            self.assertEqual([f["name"] for f in found], ["SampleAlice 09 24 26 SB.pdf"])
            self.assertEqual(found[0]["kind"], "draft")


if __name__ == "__main__":
    unittest.main()
