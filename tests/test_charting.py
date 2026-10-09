import os
import tempfile
import unittest
from datetime import date

from recordfinder.charting import coding, compose, dictation, note, phrases, scribe, wording


class SchemaTests(unittest.TestCase):
    def test_every_pdf_field_is_in_the_schema(self):
        import pymupdf
        names = set()
        with pymupdf.open(note.TEMPLATE_PATH) as doc:
            for page in doc:
                for w in page.widgets():
                    if w.field_type_string != "Button":
                        names.add(w.field_name)
        self.assertEqual(names, set(note.FIELDS))
        self.assertEqual(len(note.FIELDS), 525)

    def test_exam_rows_point_at_real_fields(self):
        for row in note.SCHEMA["exam_rows"]:
            for key in ("normal", "abnormal", "findings"):
                if row[key]:
                    self.assertIn(row[key], note.FIELDS, row)
        self.assertEqual(note.SCHEMA["exam_rows"][-1]["label"], "Normal based gait")


class NoteTests(unittest.TestCase):
    def test_fill_and_read_round_trip(self):
        values = note.blank()
        values.update({"Name": "Sample, Alice", "DOB": "01/02/1960", "Date": "09/24/2026", "neuro-progress": True,
                       "neuro-consult": False, "dizziness": True, "worse-standing": True, "duration-seconds": True,
                       "const-bp": "128/76", "appt-time": "40", "impression-plan-descr": "1) Dizziness - Monitor clinically.",
                       "Imp/MP": note.FIELDS["Imp/MP"]["options"][0], "imp-mp": True, "abnormal39": "X", "findings38": "Mild sway on Romberg"})
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "SampleAlice 09 24 26.pdf")
            note.fill(values, out)
            back = note.read(out)
        for k in ("Name", "DOB", "Date", "const-bp", "appt-time", "impression-plan-descr", "Imp/MP", "abnormal39", "findings38"):
            self.assertEqual(back[k], values[k], k)
        for k in ("neuro-progress", "dizziness", "worse-standing", "duration-seconds", "imp-mp"):
            self.assertTrue(back[k], k)
        self.assertFalse(back["neuro-consult"])
        self.assertFalse(back["worse-walking"])
        self.assertTrue(back["normal1"])        # the form's default stays

    def test_pt_referral_sync_and_unhidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "pt_test.pdf")
            note.fill({"referral-for": "Vestibular PT. Dx: gait and balance difficulties."}, out)
            back = note.read(out)
            self.assertTrue(back["PTcheck"])
            self.assertEqual(back["referral-for"], "Vestibular PT. Dx: gait and balance difficulties.")

    def test_the_name_keeps_the_forms_bold_font(self):
        import pymupdf
        values = note.blank()
        values.update({"Name": "Sample, Alice", "DOB": "01/02/1960", "Date": "09/24/2026"})
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "SampleAlice 09 24 26.pdf")
            note.fill(values, out)
            with pymupdf.open(out) as doc:
                for w in doc[0].widgets():
                    if w.field_name in ("Name", "DOB", "Date"):
                        ap = int(doc.xref_get_key(w.xref, "AP/N")[1].split()[0])
                        self.assertIn(b"/HeBo ", doc.xref_stream(ap), w.field_name)
                        self.assertTrue(doc.xref_get_key(w.xref, "DA")[1].startswith("/HeBo"), w.field_name)
            self.assertEqual(note.read(out)["Name"], "Sample, Alice")
            self.assertTrue(note.render_page(out, 0, 0.5).startswith(b"\x89PNG"))

    def test_long_narrative_gets_readable_continuation_pages(self):
        import pymupdf
        values = note.blank()
        ending = "Unique final plan sentence for the continuation test."
        values.update({"Name": "Sample, Alice", "Date": "09/24/2026",
                       "plan-descr": ("Continue vestibular rehabilitation and record symptoms after each session. " * 45) + ending})
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "long-note.pdf")
            with pymupdf.open(note.TEMPLATE_PATH) as template:
                template_pages = len(template)
            note.fill(values, out)
            with pymupdf.open(out) as doc:
                self.assertGreater(len(doc), template_pages)
                continuation = "\n".join(page.get_text() for page in doc[template_pages:])
            self.assertIn("NOTE CONTINUATION", continuation)
            self.assertIn("Plan notes", continuation)
            self.assertIn(ending, continuation.replace("\n", " "))
            self.assertEqual(note.read(out)["plan-descr"], values["plan-descr"])

    def test_filename_matches_the_office_convention(self):
        self.assertEqual(note.filename("Acevedo De Venegas", "Socorro", date(2025, 10, 2)), "AcevedoDeVenegasSocorro 10 02 25.pdf")
        self.assertEqual(note.filename("O'Brien", "Mary-Anne", date(2026, 1, 5)), "OBrienMaryAnne 01 05 26.pdf")

    def test_carry_forward_keeps_history_and_resets_the_visit(self):
        prev = note.blank()
        prev.update({"Name": "Sample, Bob", "DOB": "03/04/1950", "hist-diab": True, "meds-descr": "Metformin 500mg BID", "hpi-male": True,
                     "ChiefComplaint": "Dizziness", "Date": "06/01/2026",
                     "x-study1": "CDP", "neuro-consult": True, "neuro-progress": False, "dizziness": True, "worse-standing": True,
                     "appt-time": "60", "const-bp": "140/90"})
        new = note.carry_forward(prev, date(2026, 9, 24))
        self.assertTrue(new["hist-diab"])
        self.assertEqual(new["meds-descr"], "Metformin 500mg BID")
        self.assertEqual(new["x-study1"], "CDP")
        self.assertTrue(new["neuro-progress"])
        self.assertFalse(new["neuro-consult"])
        self.assertFalse(new["dizziness"])
        self.assertFalse(new["worse-standing"])
        self.assertEqual(new["const-bp"], "")
        self.assertEqual(new["appt-time"], "")
        self.assertFalse(new["imp-mp"])
        self.assertTrue(new["ChiefComplaint-descr"].startswith("Mr. Sample is a 76 y/o ***HD male presenting to the clinic today for a follow-up on"))
        self.assertIn("No other focal motor, sensory, or CN deficits reported at this time.", new["ChiefComplaint-descr"])
        self.assertEqual(new["Date"], "09/24/2026")
        self.assertEqual(new["HPI-age"], "76")
        self.assertTrue(new["hist-nochange"])

    def test_impressions_are_parsed(self):
        imps = note.impressions("1) Dizziness and lack of coordination - Monitor clinically.\r2) Gait difficulties - Secondary to #1.\rKeep balance log.")
        self.assertEqual([(i.number, i.diagnosis) for i in imps], [(1, "Dizziness and lack of coordination"), (2, "Gait difficulties")])
        self.assertEqual(imps[1].plan, "Secondary to #1. Keep balance log.")


class CodingTests(unittest.TestCase):
    def note(self, **over):
        v = note.blank()
        v.update({"neuro-consult": False, "neuro-progress": True, "appt-time": "40", "imp-mp": True,
                  "Imp/MP": note.FIELDS["Imp/MP"]["options"][0], "Date": "09/24/2026", "next-appt": "4",
                  "impression-plan-descr": "1) Dizziness and lack of coordination - Monitor clinically.\n2) Complex BPPV - Epley today."})
        v.update(over)
        return v

    def test_established_visit_by_time(self):
        c = coding.code_note(self.note())
        self.assertEqual(c.em.code, "99215")
        self.assertEqual(c.em.modifier, "")
        self.assertEqual([x[1] for x in c.icd], ["R42", "H81.10"])
        c = coding.code_note(self.note(**{"appt-time": "38"}))
        self.assertEqual(c.em.code, "99214")
        self.assertIn("99215 starts at 40", c.next_level)

    def test_new_patient_and_prolonged(self):
        c = coding.code_note(self.note(**{"neuro-consult": True, "neuro-progress": False, "appt-time": "60", "imp-mp": False}))
        self.assertEqual(c.em.code, "99205")
        self.assertEqual(c.addons, [])
        c = coding.code_note(self.note(**{"neuro-consult": True, "neuro-progress": False, "appt-time": "80", "imp-mp": False}))
        self.assertEqual([a.code for a in c.addons], ["99417"])
        c = coding.code_note(self.note(**{"neuro-consult": True, "neuro-progress": False, "appt-time": "80", "imp-mp": False}), payer="Medicare")
        self.assertEqual(c.addons, [])

    def test_oculography_separate_time_adds_the_procedure_and_modifier_25(self):
        c = coding.code_note(self.note(**{"time-proc1": True, "addnl-time": note.FIELDS["addnl-time"]["options"][0],
                                          "goggle-treatment-x": "Right posterior canal BPPV: Epley maneuver performed.", "imp-rightpost": True}))
        self.assertEqual(c.em.modifier, "25")
        self.assertEqual([p.code for p in c.procedures], ["92542", "95992"])

    def test_goggles_and_treatment_excludes_chair_on_superbill(self):
        c = coding.code_note(self.note(**{
            "see-epley": True,
            "goggle-dr": "Jake Perkins, PA-C",
            "dixhall-right-torsion": True,
            "goggle-treatment-x": "Canalith repositioning procedure (Epley) performed.",
            "imp-rightpost": True,
            "time-proc1": True,
            "addnl-time": note.FIELDS["addnl-time"]["options"][0]
        }))
        codes = [p.code for p in c.procedures]
        self.assertNotIn("92546", codes)
        self.assertIn("92542", codes)
        self.assertIn("95992", codes)

    def test_chair_testing_without_treatment_bills_chair_code(self):
        c = coding.code_note(self.note(**{
            "see-epley": True,
            "goggle-treatment-x": "Diagnostic testing only. No repositioning treatment required.",
            "time-proc1": True,
            "addnl-time": note.FIELDS["addnl-time"]["options"][0]
        }))
        codes = [p.code for p in c.procedures]
        self.assertIn("92546", codes)
        self.assertNotIn("92542", codes)
        self.assertNotIn("95992", codes)

    def test_g2211(self):
        c = coding.code_note(self.note(**{"time-proc1": True, "addnl-time": "Longitudinal services were provided to patient in clinic today"}))
        self.assertEqual([a.code for a in c.addons], ["G2211"])
        c = coding.code_note(self.note(**{"neuro-consult": True, "neuro-progress": False, "time-proc1": True,
                                          "addnl-time": "Longitudinal services were provided to patient in clinic today"}))
        self.assertEqual(c.addons, [])
        self.assertTrue(any("G2211" in w for w in c.warnings))

    def test_missed_procedure_is_flagged(self):
        c = coding.code_note(self.note(**{"goggle-dr": "Jake Perkins, PA-C", "dixhall-right-torsion": True}))
        self.assertTrue(any("separate-time" in w and "oculography" in w for w in c.warnings))
        c = coding.code_note(self.note(**{"debris-cerumen": True}))
        self.assertTrue(any("Cerumen" in w for w in c.warnings))

    def test_incident_to_on_a_new_patient_is_flagged(self):
        c = coding.code_note(self.note(**{"neuro-consult": True, "neuro-progress": False}))
        self.assertTrue(any("incident-to" in w for w in c.warnings))


class DictationTests(unittest.TestCase):
    def test_symptom_checkboxes(self):
        it = dictation.interpret("dizziness, worse with standing and walking in the dark, lasts seconds, paroxysmal, associated with vertigo", "dizziness")
        for k in ("dizziness", "worse-standing", "worse-walking-in-dark", "duration-seconds", "course-parox", "assoc-vertigo"):
            self.assertTrue(it.set.get(k), k)
        self.assertFalse(it.set.get("dizziness-denies", False))

    def test_denial(self):
        it = dictation.interpret("denies hearing loss", "hearing")
        self.assertTrue(it.set.get("hearingloss-denies"))
        self.assertFalse(it.set.get("hearingloss"))

    def test_vitals_and_reflexes(self):
        it = dictation.interpret("blood pressure 128 over 76, heart rate 70, weight 160, height 5 foot 6, reflexes two plus throughout", "exam")
        self.assertEqual(it.set["const-bp"], "128/76")
        self.assertEqual(it.set["const-hr"], "70")
        self.assertEqual(it.set["WEIGHT"], "160")
        self.assertEqual((it.set["HEIGHT-ft"], it.set["HEIGHT-in"]), ("5", "6"))
        self.assertEqual(it.set["dtr-l-ach"], "2+")

    def test_impressions_and_plan(self):
        it = dictation.interpret("impression one dizziness and lack of coordination dash monitor clinically two complex bppv dash epley today", "impression")
        self.assertIn("1) Dizziness and lack of coordination - Monitor clinically", it.append["impression-plan-descr"])
        self.assertIn("2) Complex BPPV - Epley today", it.append["impression-plan-descr"])      # acronyms and eponyms kept
        it = dictation.interpret("total time 40 minutes, longitudinal services were provided, return in 4 weeks", "plan")
        self.assertEqual(it.set["appt-time"], "40")
        self.assertTrue(it.set["time-proc1"])
        self.assertIn("Longitudinal", it.set["addnl-time"])
        self.assertEqual(it.set["next-appt"], "4")
        self.assertTrue(it.set["next-appt-weeks"])

    def test_denial_scope_is_the_clause_after_denies(self):
        it = dictation.interpret("Dizziness, worse withstanding and walking in the dark, last seconds, paroxysmal, associated with vertigo, denies hearing loss.", "dizziness")
        for k in ("dizziness", "worse-standing", "worse-walking-in-dark", "duration-seconds", "course-parox", "assoc-vertigo", "hearingloss-denies"):
            self.assertTrue(it.set.get(k), k)
        self.assertFalse(it.set.get("course-worsening", False))
        self.assertFalse(it.set.get("hearingloss", True))
        self.assertNotIn("DizzyDesc", it.append)

    def test_denies_twin_boxes_and_falls(self):
        it = dictation.interpret("gait: worse turning corners, better sitting or resting, denies leg weakness, no falls", "gait")
        self.assertTrue(it.set.get("ga-worse-corners") and it.set.get("ga-better-sitrest"))
        self.assertTrue(it.set.get("ga-assoc-weakness-denies"))
        self.assertFalse(it.set.get("ga-assoc-weakness", False))
        self.assertTrue(it.set.get("ga-falls-no"))

    def test_free_text_goes_to_the_hpi(self):
        it = dictation.interpret("she reports two episodes of spinning last week while rolling over in bed", "hpi")
        self.assertIn("ChiefComplaint-descr", it.append)
        self.assertTrue(it.append["ChiefComplaint-descr"].startswith("She reports"))

    def test_narrative_is_written_in_the_clinics_wording(self):
        it = dictation.interpret("patient says she has had dizziness for about two months, um, maybe hearing loss in the left ear", "hpi")
        self.assertEqual(it.append["ChiefComplaint-descr"], "Pt reports she has had dizziness for ~2 months, possible hearing loss AS.")
        it = dictation.interpret("she mentions the spinning comes on when she rolls over, not sure how long it lasts", "hpi")
        self.assertEqual(it.append["ChiefComplaint-descr"], "She reports the spinning comes on when she rolls over, uncertain how long it lasts.")
        it = dictation.interpret("blood pressure 128 over 76, she says it lasts seconds to", "exam")
        self.assertEqual(it.set["const-bp"], "128/76")                     # vitals still read from the words as said

    def test_section_switch(self):
        it = dictation.interpret("headache: frontal, throbbing, photophobia", "hpi")
        self.assertEqual(it.section, "headache")
        self.assertTrue(it.set.get("h-frontal") and it.set.get("h-course-throb") and it.set.get("h-assoc-photo"))


    def test_exam_in_the_practices_wording(self):
        it = dictation.interpret("exam otherwise normal, mild sway on Romberg, decreased hearing to finger friction rub and "
                                 "128 hertz tuning fork AS, regular rate and rhythm", "exam")
        self.assertEqual((it.set["abnormal40"], it.set["normal40"], it.set["findings39"]), ("X", False, "Mild sway on Romberg"))
        self.assertEqual(it.set["findings27"], "Decreased hearing to finger friction rub and 128Hz tuning fork AS")
        self.assertEqual((it.set["normal8"], it.set["findings8"]), (True, "RRR"))
        self.assertTrue(it.set["normal3"])                # "otherwise normal"
        self.assertNotIn("ChiefComplaint-descr", it.append)

    def test_a_whole_visit_in_one_dictation(self):
        it = dictation.interpret("She has had spinning for two weeks when rolling over in bed. Next section dizziness, worse with "
                                 "transitioning, lasts seconds, paroxysmal. Section exam, otherwise unremarkable, left gaze evoked "
                                 "nystagmus. Impression one dizziness and lack of coordination dash keep balance log. Build the note.", "hpi")
        self.assertTrue(it.append["ChiefComplaint-descr"].startswith("She has had spinning"))
        for k in ("dizziness", "worse-transitioning", "duration-seconds", "course-parox"):
            self.assertTrue(it.set.get(k), k)
        self.assertEqual((it.set["abnormal24"], it.set["findings24"]), ("X", "Left gaze evoked nystagmus"))
        self.assertIn("1) Dizziness and lack of coordination - Keep balance log", it.append["impression-plan-descr"])
        self.assertTrue(it.build)

    def test_a_plain_mention_is_not_a_new_section(self):
        it = dictation.interpret("she will be rechecked at the next exam for her dizziness", "hpi")
        self.assertEqual(it.section, "hpi")
        self.assertFalse(it.build)

    def test_imaging_ordered_goes_to_the_order_boxes_and_the_plan(self):
        it = dictation.interpret("order an MRI brain with and without", "plan")
        self.assertEqual(it.imaging, ["MRI Brain with and w/o contrast 70553"])
        self.assertEqual(it.studies, [])
        self.assertIn("MRI brain", it.append["plan-descr"])                       # the plan says what was ordered
        it = dictation.interpret("get an MRA neck", "plan")
        self.assertEqual(it.imaging, ["MRA Neck without contrast"])
        it = dictation.interpret("let's image the IACs", "plan")
        self.assertEqual(it.imaging, ["MRI Brain IAC \u2013Screening Protocol 70551"])
        it = dictation.interpret("we'll get a CTA head and neck and a carotid duplex, return in 3 months", "plan")
        self.assertEqual(it.imaging, ["CTA Head and Neck", "Carotid duplex ultrasound"])
        self.assertEqual(it.set["next-appt"], "3")
        it = dictation.interpret("no imaging needed at this time", "plan")
        self.assertEqual(it.imaging, ["No Imaging Required."])
        # said somewhere other than the plan, the order is still written into the plan box
        it = dictation.interpret("we will order a CT head without contrast", "hpi")
        self.assertEqual(it.imaging, ["CT Brain without contrast 70450"])
        self.assertIn("- Ordered: CT Brain without contrast 70450", it.append["plan-descr"])
        # in a numbered impression the words stay on the impression line too
        it = dictation.interpret("impression one dizziness dash order MRI brain with and without contrast two bppv dash epley today", "impression")
        self.assertEqual(it.imaging, ["MRI Brain with and w/o contrast 70553"])
        self.assertIn("1) Dizziness - Order MRI brain with and without contrast", it.append["impression-plan-descr"])

    def test_someone_elses_order_or_a_scan_had_is_not_todays_order(self):
        it = dictation.interpret("PCP ordered an MRI", "hpi")
        self.assertEqual((it.imaging, it.studies), ([], []))
        it = dictation.interpret("we will not order an MRI at this time", "plan")
        self.assertEqual(it.imaging, [])
        it = dictation.interpret("she had an MRI at the ER which was normal, she still has spinning when rolling over", "hpi")
        self.assertEqual(it.imaging, [])
        self.assertEqual(it.studies, [{"name": "MRI Brain @ ER", "date": "", "result": "Normal"}])
        self.assertTrue(it.append["ChiefComplaint-descr"].startswith("She had an MRI at the ER"))

    def test_reviewed_results_go_to_the_study_rows(self):
        it = dictation.interpret("MRI brain from June was unremarkable", "studies")
        self.assertEqual(it.studies, [{"name": "MRI Brain", "date": "June", "result": "Unremarkable."}])
        self.assertEqual(it.imaging, [])
        self.assertNotIn("x-study1-result", it.append)                              # the rows are the narrative here
        it = dictation.interpret("VNG showed left caloric weakness", "studies")
        self.assertEqual(it.studies, [{"name": "VNG", "date": "", "result": "Left caloric weakness."}])
        it = dictation.interpret("Zeto EEG was normal", "studies")
        self.assertEqual(it.studies, [{"name": "Zeto EEG", "date": "", "result": "Normal"}])
        it = dictation.interpret("MRI brain from June was unremarkable and the VNG showed left caloric weakness. "
                                 "Zeto EEG from 8/12/26 was normal.", "studies")
        self.assertEqual([s["name"] for s in it.studies], ["MRI Brain", "VNG", "Zeto EEG"])
        self.assertEqual(it.studies[2]["date"], "8/12/26")
        self.assertEqual(it.append, {})
        it = dictation.interpret("reviewed the CDP from 03/02/26: composite score of 54", "studies")
        self.assertEqual(it.studies, [{"name": "CDP", "date": "03/02/26", "result": "Composite score of 54."}])
        it = dictation.interpret("lower extremity EMG showed a length dependent axonal polyneuropathy", "hpi")
        self.assertEqual(it.studies[0]["name"], "EMG/NCV BLE")
        # a result's "normal" / "left" ticks no box, and the symptom boxes still read
        it = dictation.interpret("MRI brain was normal. Dizziness worse with standing, lasts seconds.", "dizziness")
        self.assertEqual(it.studies[0]["result"], "Normal")
        self.assertTrue(it.set.get("worse-standing") and it.set.get("duration-seconds"))
        self.assertFalse(any(k.startswith("imp-") and v for k, v in it.set.items()))
        self.assertIn("imaging", it.as_dict())
        self.assertIn("studies", it.as_dict())


class PhraseTests(unittest.TestCase):
    def test_exam_wording_tables_use_the_practices_own_phrases(self):
        for fld, text in phrases.EXAM_NORMAL_TEXT.items():
            self.assertIn(text, phrases.EXAM_NORMALS[fld], fld)
            self.assertNotIn(text, phrases.EXAM_ABNORMAL.get(fld, []), fld)
        for fld, items in phrases.EXAM_ABNORMAL.items():
            for text in items:
                self.assertIn(text, phrases.EXAM_NORMALS[fld], fld)


def _visit(**extra):
    v = note.blank()
    v.update({"Name": "Sample, Alice", "DOB": "01/02/1960", "Date": "09/24/2026", "hpi-female": True, "neuro-consult": True})
    v.update(extra)
    return v


class ComposeTests(unittest.TestCase):
    def proposals(self, v, previous=None, last_seen=""):
        c = compose.compose_note(v, previous, last_seen)
        return {p.id: p for p in c.proposals}, c.todo

    def test_hpi_is_written_from_the_boxes(self):
        v = _visit(**{"hist-hypt": True, "hist-diab": True, "dizziness": True, "assoc-vertigo": True, "course-parox": True,
                      "duration-seconds": True, "worse-transitioning": True, "course-stable": True, "hearingloss-denies": True,
                      "tinnitus-denies": True, "ga-falls-no": True})
        props, todo = self.proposals(v)
        hpi = props["hpi"].text
        self.assertTrue(hpi.startswith("Ms. Sample is a 66 y/o ***HD female presenting to the clinic today for a consultation on "
                                       "dizziness. PMHx significant for HTN and DM."), hpi)
        self.assertIn("Pt reports paroxysmal vertigo lasting seconds, worse w/ transitioning to standing from sitting or supine.", hpi)
        self.assertIn("Sxs are stable.", hpi)
        self.assertIn("Denies any recent falls.", hpi)
        self.assertIn("Denies hearing loss or tinnitus.", hpi)         # only what the boxes deny: no fluctuation or aural fullness
        self.assertNotIn("aural fullness", hpi)
        self.assertTrue(hpi.endswith(phrases.HPI_CLOSERS[0]))
        self.assertNotIn("****", hpi)                     # the blank form's placeholder opening is replaced, not kept
        self.assertTrue(any("handedness" in t for t in todo))
        self.assertEqual(props["cc"].text, "Dizziness")

    def test_the_providers_own_text_is_kept_and_a_rebuild_replaces_only_what_it_wrote(self):
        v = _visit(**{"dizziness": True, "assoc-vertigo": True})
        v["ChiefComplaint-descr"] = "Ms. Sample is a 66 y/o RHD female presenting for dizziness.\nShe first noticed it rolling over in bed."
        props, _ = self.proposals(v)
        first = props["hpi"]
        self.assertTrue(first.text.startswith("Ms. Sample is a 66 y/o RHD female presenting for dizziness."))
        self.assertIn("She first noticed it rolling over in bed.", first.text)
        self.assertIn("Pt reports vertigo.", first.text)
        v["ChiefComplaint-descr"] = first.text
        v["assoc-vertigo"], v["assoc-diseq"] = False, True
        props, _ = self.proposals(v, {"ChiefComplaint-descr": first.generated})
        again = props["hpi"].text
        self.assertIn("Pt reports disequilibrium.", again)
        self.assertNotIn("Pt reports vertigo.", again)
        self.assertEqual(again.count("She first noticed it"), 1)
        v["ChiefComplaint-descr"] = again
        props, _ = self.proposals(v, {"ChiefComplaint-descr": props["hpi"].generated})
        self.assertNotIn("hpi", props)                    # nothing new to write

    def test_follow_up_opening(self):
        v = _visit(**{"neuro-consult": False, "neuro-progress": True, "hpi-male": True, "hpi-female": False, "ChiefComplaint": "Dizziness"})
        props, _ = self.proposals(v, last_seen="06/01/2026")
        self.assertTrue(props["hpi"].text.startswith("Mr. Sample is a 66 y/o ***HD male presenting to the clinic today for a "
                                                     "follow-up on dizziness. Last seen on 06/01/2026."))

    def test_impressions_from_the_boxes_and_plan(self):
        v = _visit(**{"dizziness": True, "gaitataxia": True, "imp-atyp": True, "supine-center-upbeat": True, "plan-balancelog": True,
                      "plan-fall": True, "ga-assoc-weakness-denies": True, "ga-assoc-sensory-denies": True,
                      "ga-assoc-bowel-denies": True, "ga-assoc-bladder-denies": True})
        props, _ = self.proposals(v)
        lines = props["impression"].text.split("\n")
        self.assertTrue(lines[0].startswith("1) Dizziness and lack of coordination - Cursory DixHallpike test"))
        self.assertIn("atypical upbeat nystagmus. Keep balance log. RTC with worsening symptoms.", lines[0])
        self.assertEqual(lines[1], "2) Gait and balance difficulties - Secondary to #1. Strict fall precautions. " + phrases.CCS_NEGATIVE)
        self.assertEqual([i.diagnosis for i in note.impressions(props["impression"].text)],
                         ["Dizziness and lack of coordination", "Gait and balance difficulties"])

    def test_carried_impressions_stay_and_new_ones_follow(self):
        v = _visit(**{"impression-plan-descr": "1) Dizziness and lack of coordination - Monitor clinically.", "dizziness": True,
                      "tinnitus": True, "t-bilat": True, "plan-return": False})
        props, todo = self.proposals(v)
        self.assertEqual(props["impression"].text, "1) Dizziness and lack of coordination - Monitor clinically.\n2) Tinnitus AU - ***")
        self.assertTrue(any("#2" in t for t in todo))
        self.assertEqual(coding.icd_for("Tinnitus AU"), "H93.13")

    def test_it_never_claims_a_maneuver(self):
        v = _visit(**{"dizziness": True, "imp-rightpost": True})
        props, todo = self.proposals(v)
        self.assertNotIn("treatment", props)
        self.assertNotIn("Epley", props["impression"].text)
        self.assertTrue(props["impression"].text.startswith("1) Right posterior canal BPPV"))
        self.assertTrue(any("treatment" in t for t in todo))
        v = _visit(**{"imp-normal": True})
        props, _ = self.proposals(v)
        self.assertEqual(props["treatment"].text, "Normal study. No treatment indicated.")

    def test_exam_wording_only_on_blank_normal_rows(self):
        v = _visit(**{"findings8": "Irregular", "abnormal40": "X", "normal40": False})
        props, todo = self.proposals(v)
        changes = props["exam"].changes
        self.assertEqual(changes["findings12"], "A&O x 3")
        self.assertNotIn("findings8", changes)            # something is already written there
        self.assertNotIn("findings39", changes)           # abnormal row: the finding is the provider's
        self.assertTrue(any("Normal based gait" in t for t in todo))

    def test_a_dictated_opening_becomes_the_practices_opening(self):
        said = ("64-year-old right-handed female presents for evaluation of intermittent bilateral hand tremor for eight months, "
                "worse with action and stress, denies resting tremor, weakness, numbness, gait instability or recent falls, and no "
                "prior neurologic history. Symptoms are slowly progressive and beginning to interfere with writing and eating.")
        it = dictation.interpret(said, "hpi")
        self.assertEqual(it.append["ChiefComplaint-descr"], wording.clinical(said))     # the clinic's wording, not a transcript
        self.assertTrue(it.append["ChiefComplaint-descr"].startswith(
            "64 y/o RHD female presents for evaluation of intermittent bilateral hand tremor for 8 months, worse w/ action and stress, "
            "denies resting tremor, weakness, numbness, gait instability or recent falls"))   # "denies" and its whole list stay in
        self.assertEqual((it.set["HPI-age"], it.set["hpi-female"]), ("64", True))
        v = note.blank()
        v.update({"Date": "10/06/2026", "neuro-consult": True, "ga-falls-no": True, "gaitataxia-denies": True})
        v["ChiefComplaint-descr"] = v["ChiefComplaint-descr"].replace("\r", "\n") + "\n" + said
        props, todo = self.proposals(v)
        hpi = props["hpi"].text
        # the clinic's lead and the reason only; what the sentence says after the reason follows as sentences of its own
        self.assertTrue(hpi.startswith("Ms. **** is a 64 y/o RHD female presenting to the clinic today for a consultation on intermittent "
                                       "bilateral hand tremor. PMHx significant for ****.\n\nOnset 8 months ago. Worse w/ action and stress, "
                                       "denies resting tremor, weakness, numbness, gait instability or recent falls, and no prior neurologic "
                                       "history. Sxs are slowly progressive and beginning to interfere w/ writing and eating."), hpi)
        self.assertNotIn("Mr./Ms.", hpi)                                   # the blank form's opening is gone, not doubled
        self.assertNotIn("Denies any recent falls.", hpi)                   # already in the dictation
        self.assertEqual(props["hpi"].changes["HPI-age"], "64")
        self.assertTrue(props["hpi"].changes["hpi-female"])
        self.assertEqual(props["cc"].text, "Intermittent bilateral hand tremor")
        self.assertTrue(any("name" in t and "past medical history" in t for t in todo))

    def test_an_existing_opening_is_refreshed_not_replaced(self):
        v = _visit(**{"ChiefComplaint-descr": "Ms. **** is a 64 y/o RHD female presenting to the clinic today for a consultation on "
                                              "hand tremor for 8 months, worse with action. PMHx significant for HTN."})
        props, _ = self.proposals(v)
        self.assertTrue(props["hpi"].text.startswith("Ms. Sample is a 66 y/o RHD female presenting to the clinic today for a "
                                                     "consultation on hand tremor for 8 months, worse with action. PMHx significant for HTN."))

    def test_placeholders_are_a_billing_check(self):
        v = _visit(**{"impression-plan-descr": "1) Tinnitus AU - ***"})
        self.assertTrue(any("Placeholders" in w for w in coding.code_note(v).warnings))

    def test_the_hpi_comes_in_parts_one_per_complaint(self):
        v = _visit(**{"dizziness": True, "assoc-vertigo": True, "duration-seconds": True, "tinnitus": True, "t-left": True,
                      "hearingloss-denies": True, "ga-falls-no": True})
        v["ChiefComplaint-descr"] = v["ChiefComplaint-descr"].replace("\r", "\n") + "\nShe first noticed it rolling over in bed."
        props, _ = self.proposals(v)
        hpi = props["hpi"]
        keys = [p["key"] for p in hpi.parts]
        self.assertEqual(keys[:3], ["opening", "dizziness", "tinnitus"])
        self.assertIn("notes", keys)
        self.assertIn("negatives", keys)
        self.assertEqual(keys[-1], "closer")
        by = {p["key"]: p for p in hpi.parts}
        self.assertEqual(by["dizziness"]["text"], "Pt reports vertigo lasting seconds.")
        self.assertEqual(by["tinnitus"]["text"], "Reports tinnitus AS.")
        self.assertEqual(by["notes"]["text"], "She first noticed it rolling over in bed.")
        self.assertTrue(by["notes"]["fixed"])                 # the provider's own words can't be unticked
        self.assertFalse(by["opening"]["fixed"])              # a built opening can
        self.assertEqual(by["negatives"]["text"], "Denies any recent falls. Denies hearing loss.")
        # ticked all, the parts are the built text; untick one and the paragraph is gone
        self.assertEqual("\n\n".join(p["text"] for p in hpi.parts), hpi.text)
        self.assertNotIn("Reports tinnitus AS.", "\n\n".join(p["text"] for p in hpi.parts if p["key"] != "tinnitus"))
        self.assertIn("parts", hpi.as_dict())

    def test_a_scope_hpi_in_the_story_is_built_from(self):
        # The Scope note's HPI is put into the story (Build does this on paste): the builder then drafts the HPI from it
        # like typed or dictated notes, with the complaint's paragraph once its boxes are ticked.
        v = _visit(**{"neuro-consult": False, "neuro-progress": True, "hpi-male": True, "hpi-female": False, "ChiefComplaint": "Dizziness"})
        v["ChiefComplaint-descr"] = v["ChiefComplaint-descr"].replace("\r", "\n") + "\n\n" + \
            "Patient reports episodic room-spinning dizziness when rolling over in bed, lasting seconds. Denies hearing loss."
        props, _ = self.proposals(v, last_seen="06/01/2026")
        self.assertIn("story", props)                          # the boxes the notes tick
        self.assertIn("dizziness", props["story"].changes)
        hpi = props["hpi"].text
        self.assertTrue(hpi.startswith("Mr. Sample is a 66 y/o ***HD male presenting to the clinic today for a follow-up on dizziness. Last seen on 06/01/2026."), hpi)
        self.assertIn("Pt reports episodic room-spinning dizziness when rolling over in bed, lasting seconds. Denies hearing loss.", hpi)
        self.assertNotIn("Mr./Ms.", hpi)                       # the blank form's opening is gone
        for k, val in props["story"].changes.items():
            v[k] = val
        v["ChiefComplaint-descr"] = hpi
        props, _ = self.proposals(v, {"ChiefComplaint-descr": props["hpi"].generated}, last_seen="06/01/2026")
        again = props["hpi"]
        self.assertTrue(any(p["key"] == "dizziness" for p in again.parts), [p["key"] for p in again.parts])
        self.assertIn("vertigo", again.text)
        self.assertEqual(again.text.count("rolling over in bed"), 1)

    def test_the_falls_check_names_one_question(self):
        v = _visit(**{"gaitataxia": True})
        v["ChiefComplaint-descr"] = v["ChiefComplaint-descr"].replace("\r", "\n") + "\nReports a fall in June. Denies any falls."
        c = compose.compose_note(v)
        self.assertTrue(any(x.startswith("Said both ways: falls: ") for x in c.check), c.check)

    def test_the_hpi_decides_the_complaints_and_their_boxes(self):
        v = {"neuro-consult": True, "ChiefComplaint-descr": "Spinning vertigo for 3 weeks, worse with head turns and rolling over in bed. "
                                                            "Some ringing in the left ear. No headaches."}
        c = compose.compose_note(v)
        states = {t["id"]: t["state"] for t in c.topics}
        self.assertEqual(states, {"dizziness": "present", "hearing": "absent", "tinnitus": "present", "gait": "absent", "headache": "denied"})
        self.assertIn("ringing", next(t["why"] for t in c.topics if t["id"] == "tinnitus"))
        items = next(p for p in c.proposals if p.id == "story").items
        self.assertTrue(items and all("section" in it for it in items))
        self.assertTrue(any(it["section"] == "dizziness" and "worse-headturn" in "".join(it["changes"]) or
                            it["section"] == "dizziness" and it["changes"].get("dizziness") for it in items))
        self.assertTrue(any(it["section"] == "tinnitus" for it in items))
        # a box the provider ticked by hand counts, with no notes at all
        c = compose.compose_note({"gaitataxia": True})
        self.assertEqual({t["id"]: t["state"] for t in c.topics}["gait"], "present")



class VisitRecordingTests(unittest.TestCase):
    def test_parts_are_transcribed_in_the_background_and_joined_in_order(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(3):
                path = os.path.join(tmp, f"part{i}.wav")
                open(path, "wb").close()
                paths.append(path)
            with mock.patch.object(dictation, "transcribe", side_effect=lambda path, *_a: f"said in {os.path.basename(path)[:5]}."):
                for i in (2, 0, 1):                                    # parts can arrive out of order
                    scribe.add_part("visit-test", i, paths[i])
                text = scribe.finish("visit-test", timeout=20)
            self.assertEqual(text, "said in part0. said in part1. said in part2.")
            self.assertFalse(any(os.path.exists(x) for x in paths))      # the audio is deleted once transcribed

    def test_a_conversation_is_read_by_its_answers(self):
        t = ("What brings you in? I've been getting dizzy when I stand up, the room spins. Any ringing in your ears? No, nothing like that. "
             "How about your hearing? It's fine. Have you had any falls? No. Any headaches? Yeah, a few times a week. "
             "Your blood pressure is 128 over 76.")
        r = scribe.read_visit(t)
        self.assertTrue(r.set["dizziness"])
        self.assertTrue(r.set["tinnitus-denies"])                         # "Any ringing?" "No." is a denial, not tinnitus
        self.assertFalse(r.set["tinnitus"])
        self.assertTrue(r.set["ga-falls-no"])
        self.assertTrue(r.set["headache"])
        self.assertNotIn("hearingloss", r.set)                            # "It's fine" isn't plain enough to tick
        self.assertEqual(r.set["const-bp"], "128/76")



if __name__ == "__main__":
    unittest.main()
