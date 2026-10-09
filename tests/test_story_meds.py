"""Typed notes and pasted transcripts into the note's boxes, and the medication list.

The five notes below are the kind of thing a provider types between rooms (and the kind speech recognition hands
back): hedges, contradictions, sentences that stop. Only what they say plainly may be ticked."""
import json
import os
import unittest

from recordfinder.charting import compose, dictation, medlist, note, scribe, story, wording

TREMOR = ("67-year-old with shaking in both hands maybe 6 or 8 months, worse with holding cup and writing, sometimes says happens at rest "
          "but not sure. Father had tremor maybe Parkinson's. Also feels slower lately and handwriting smaller but then says only when tired. "
          "No falls except one in yard. Caffeine 3-4 cups. Symptoms seem worse when...")
HEADACHE = ("42-year-old female with headaches started around 3 weeks ago or maybe longer, pressure behind right eye then sometimes whole head. "
            "Says blurry vision off and on, no clear double vision. Nausea once. Took ibuprofen helped a little. Has history of migraines but "
            "says this feels different because more constant. No weakness, although left arm felt heavy yesterday for about...")
NUMBNESS = ("58-year-old presents for numbness right hand and sometimes foot. Started after waking up one morning about 2 months ago, comes "
            "and goes. Mostly first 3 fingers but sometimes whole hand. Dropping objects occasionally. Neck pain for years. Denies weakness but "
            "later says grip feels weaker. No bowel or bladder changes. Had MRI somewhere maybe 2024 but does not remember results and...")
DIZZY = ("73-year-old with dizziness described as lightheaded but also room moving sometimes. Worse standing up and when turning head quickly. "
         "Started maybe January, more frequent this month. One fall but says tripped over rug. No LOC. Has ringing in left ear, maybe hearing "
         "loss. Also mentions legs feel unsteady especially at night. Blood pressure medication changed recently but unsure which one. "
         "Symptoms last seconds to...")
MEMORY = ("71-year-old brought for memory concerns, family noticed repeating questions and forgetting appointments for about a year. Patient "
          "says memory is fine and mostly stress. Still driving, got lost once but says road was closed. Misplaces phone and keys. Sleep poor, "
          "wakes several times. Mood maybe depressed since spouse died. No hallucinations. Handles medications independently although "
          "daughter says sometimes doses are missed. Unclear when symptoms first really...")


def ticked(r):
    return {k for k, v in r.set.items() if v is True}


class TypedNotesTests(unittest.TestCase):
    def test_hedged_items_are_listed_not_ticked(self):
        r = story.read_notes(DIZZY)
        self.assertTrue({"dizziness", "assoc-vertigo", "worse-standing", "worse-yaw", "tinnitus", "t-left", "ga-falls-yes"} <= ticked(r))
        self.assertNotIn("hearingloss", r.set)                         # "maybe hearing loss"
        self.assertNotIn("hist-hypt", r.set)                           # on a BP medication isn't "hypertension" said
        self.assertTrue(any("hearing loss" in u.lower() for u in r.unclear))
        self.assertEqual(r.unfinished, "Symptoms last seconds to...")
        self.assertTrue(any("Blood pressure medication" in m for m in r.med_notes))

    def test_a_contradiction_is_flagged_and_neither_side_ticked(self):
        r = story.read_notes(NUMBNESS)
        self.assertNotIn("ga-assoc-weakness-denies", ticked(r))        # "Denies weakness but later says grip feels weaker"
        self.assertTrue(any("grip feels weaker" in u for u in r.unclear))
        self.assertTrue({"ga-assoc-bowel-denies", "ga-assoc-bladder-denies"} <= ticked(r))
        self.assertNotIn("hist-cervical", r.set)                       # neck pain isn't cervical disease

    def test_details_stay_inside_the_symptom_they_describe(self):
        r = story.read_notes(HEADACHE)
        self.assertTrue({"headache", "h-course-pressure", "h-right", "h-global", "h-assoc-nausea", "hist-migraine"} <= ticked(r))
        self.assertNotIn("h-left", r.set)                              # "left arm felt heavy" isn't a left-sided headache
        self.assertNotIn("ga-assoc-weakness-denies", ticked(r))        # "No weakness, although left arm felt heavy"
        self.assertEqual(r.set.get("h-better-thesemeds"), "ibuprofen")

    def test_someone_elses_history_and_habits_are_not_the_patients(self):
        r = story.read_notes(TREMOR)
        self.assertIn("ga-falls-yes", ticked(r))                       # "No falls except one in yard"
        self.assertFalse(any(k.startswith(("hist-", "gaitataxia", "ga-assoc")) for k in ticked(r)))
        self.assertNotIn("Caffeine", r.meds)

    def test_a_note_that_is_all_concerns_ticks_nothing(self):
        r = story.read_notes(MEMORY)
        self.assertEqual(ticked(r), set())
        self.assertTrue(any("depressed" in u for u in r.unclear))
        self.assertTrue(any("doses are missed" in m for m in r.med_notes))

    def test_clinic_shorthand(self):
        r = story.read_notes("Pt c/o dizziness x3 mo, room spinning when rolling over in bed, lasts seconds. Denies hearing loss, tinnitus. "
                             "No falls. Hx HTN, DM2. Former smoker.")
        self.assertTrue({"dizziness", "assoc-vertigo", "worse-supine", "duration-seconds", "hearingloss-denies", "tinnitus-denies",
                         "ga-falls-no", "hist-hypt", "hist-diab", "social-tobacco-remote"} <= ticked(r))
        self.assertNotIn("social-tobacco", r.set)


class BuildFromTypingTests(unittest.TestCase):
    def test_a_typed_opening_becomes_the_clinics(self):
        lead, facts = compose.dictated_opening({}, TREMOR)
        self.assertEqual(lead, "Mr./Ms. **** is a 67 y/o ***HD *** presenting to the clinic today for shaking in both hands.")
        self.assertEqual((facts["age"], facts["sex"], facts["reason"]), ("67", "", "shaking in both hands"))
        self.assertTrue(facts["rest"].startswith(" maybe 6 or 8 months, worse with holding cup"))
        lead, facts = compose.dictated_opening({}, "67 yo M RHD here for f/u of BPPV.")
        self.assertEqual(lead, "Mr. **** is a 67 y/o RHD male presenting to the clinic today for a follow-up on BPPV.")
        lead, facts = compose.dictated_opening({}, "36 yo F RHD w/ hx migraine c/o dizziness x4 mo, rocking sensation.")
        self.assertEqual((facts["reason"], facts["pre"]), ("dizziness", "hx migraine"))     # the complaint is what follows c/o
        self.assertIsNone(compose.dictated_opening({}, "71 yo F, lives alone."))               # no symptom after the comma: not an opening

    def test_the_rest_of_a_typed_opening_is_kept_as_sentences(self):
        hpi = next(p for p in compose.compose_note({"ChiefComplaint-descr": TREMOR, "neuro-consult": True}).proposals if p.id == "hpi")
        opening, rest = hpi.text.split("\n\n")[:2]
        self.assertEqual(opening, "Mr./Ms. **** is a 67 y/o ***HD *** presenting to the clinic today for a consultation on shaking in "
                                  "both hands. PMHx significant for ****.")
        self.assertTrue(rest.startswith("Onset possibly 6 or 8 months ago. Worse w/ holding cup and writing, sometimes reports happens "
                                        "at rest but uncertain. Father had tremor possible Parkinson's."), rest)
        self.assertTrue(rest.endswith("Sxs seem worse when ***"))          # the unfinished thought stays visibly unfinished

    def test_typed_notes_give_boxes_to_tick_one_by_one(self):
        out = compose.compose_note({"ChiefComplaint-descr": DIZZY, "neuro-consult": True, "Name": "Sample, Ann", "tinnitus-denies": True})
        boxes = next(p for p in out.proposals if p.id == "story")
        texts = [i["text"] for i in boxes.items]
        self.assertIn("Dizziness: present", texts)
        self.assertIn("Dizziness: worse with standing", texts)
        self.assertNotIn("Tinnitus: present", texts)                   # the provider ticked "denies": a conflict, not a change
        self.assertTrue(any("Tinnitus" in t and "denies" in t for t in out.todo))
        present = next(i for i in boxes.items if i["text"] == "Dizziness: present")
        self.assertEqual(present["changes"], {"dizziness": True, "dizziness-denies": False})
        self.assertTrue(any(c.startswith("Unfinished") for c in out.check))
        self.assertTrue(any("maybe hearing loss" in c for c in out.check))

    def test_the_hpi_doesnt_repeat_what_the_story_says(self):
        v = {"ChiefComplaint-descr": "Pt c/o dizziness, room spinning, worse standing up.", "dizziness": True, "assoc-vertigo": True,
             "worse-standing": True, "neuro-consult": True}
        hpi = next(p for p in compose.compose_note(v).proposals if p.id == "hpi")
        self.assertNotIn("room spinning", hpi.text)                    # every word of it is in the ticked boxes: said once, the clinic's way
        self.assertEqual(hpi.text.count("Pt reports dizziness w/ vertigo, worse w/ standing."), 1)   # the notes say "dizziness"
        v["duration-seconds"] = True                                   # one the story doesn't say is still written out
        hpi = next(p for p in compose.compose_note(v).proposals if p.id == "hpi")
        self.assertIn("lasting seconds", hpi.text)

    def test_the_story_and_the_date_of_birth_disagree(self):
        out = compose.compose_note({"ChiefComplaint-descr": "73-year-old with dizziness.", "DOB": "02/14/1958", "Date": "10/06/2026"})
        self.assertTrue(any("73 years old" in c and "68" in c for c in out.check))
        hpi = next(p for p in out.proposals if p.id == "hpi")
        self.assertNotIn("HPI-age", hpi.changes)                       # age comes from the DOB, not the story


def slots(r):
    return {k: v for k, v in r.set.items() if k in story.SLOTS}


class SymptomSlotTests(unittest.TestCase):
    """Onset, description and frequency go into the section's text boxes, in the clinic's style, hedges kept."""

    def test_onset_and_description_from_the_notes(self):
        self.assertEqual(slots(story.read_notes(DIZZY)), {"DizzyDesc": "lightheadedness w/ room moving at times",
                                                         "DizzOnsetDate": "possibly January", "OnsetDate": "possibly January"})
        self.assertEqual(slots(story.read_notes(HEADACHE)), {"HOnsetDate": "~3 weeks ago or possibly longer",
                                                            "OnsetDate": "~3 weeks ago or possibly longer"})
        self.assertEqual(slots(story.read_notes(TREMOR)), {"OnsetDate": "possibly 6 or 8 months ago"})
        self.assertEqual(slots(story.read_notes(NUMBNESS)), {"OnsetDate": "~2 months ago"})
        self.assertEqual(slots(story.read_notes(MEMORY)), {})              # "Unclear when symptoms first really...": nothing guessed

    def test_clinic_shorthand_and_frequency(self):
        r = story.read_notes("58 yo F RHD c/o episodic vertigo x3 mo, about twice a month. Denies hearing loss.")
        self.assertEqual((r.set["DizzOnsetDate"], r.set["OnsetDate"], r.set["DizzFreq"]), ("3 months ago", "3 months ago", "~2x/month"))
        r = story.read_notes("Pt c/o headaches 3 times a week, described as pressure behind the eyes.")
        self.assertEqual((r.set["h-freq-wk"], r.set["headache-descr"]), ("3", "pressure behind the eyes"))
        r = story.read_notes("59 yo M w whooshing in L ear in time with his heartbeat x 6 wks.")
        self.assertEqual((r.set["tinnitus-descr"], r.set["TinnitusOnsetDate"]), ("whooshing", "6 weeks ago"))
        self.assertTrue(r.set["t-left"] and r.set["t-pulsatile"])
        r = story.read_notes("Chronic imbalance x yrs, worse in the dark.")
        self.assertEqual(r.set["GAOnsetDate"], "Longstanding")

    def test_a_time_about_something_else_is_not_the_onset(self):
        r = story.read_notes("84-year-old woman, unsteady for 'a couple years' getting worse, 3 falls since June.")
        self.assertNotIn("GAOnsetDate", r.set)                         # June is when the falls started
        r = story.read_notes("Dizziness. Vomiting x2 days. Started meclizine 2 weeks ago.")
        self.assertNotIn("DizzOnsetDate", r.set)
        r = story.read_notes("Dizzy for a while, not sure when it started.")
        self.assertNotIn("DizzOnsetDate", r.set)

    def test_said_two_ways_is_left_empty(self):
        r = story.read_notes("Dizziness started 2 weeks ago. Dizziness started in March.")
        self.assertNotIn("DizzOnsetDate", r.set)
        self.assertTrue(any("onset" in u for u in r.unclear))

    def test_the_boxes_proposal_offers_them_as_text(self):
        out = compose.compose_note({"ChiefComplaint-descr": DIZZY, "neuro-consult": True})
        items = {i["text"]: i for i in next(p for p in out.proposals if p.id == "story").items}
        self.assertEqual(items["Dizziness: Date of onset: possibly January"]["changes"], {"DizzOnsetDate": "possibly January"})
        self.assertIn("Dizziness: Description: lightheadedness w/ room moving at times", items)
        self.assertIn("Complaint: Date of onset: possibly January", items)
        out = compose.compose_note({"ChiefComplaint-descr": DIZZY, "DizzOnsetDate": "1/2026"})
        self.assertFalse(any("DizzOnsetDate" in i["changes"] for p in out.proposals for i in p.items))   # never over what is there


class ReaderPrecisionTests(unittest.TestCase):
    """A wrong tick is far worse than a missed one."""

    def test_a_companion_the_patient_contradicts(self):
        r = story.read_notes("Husband says she's been unsteady walking lately but she says she's fine walking.")
        self.assertFalse({"gaitataxia", "gaitataxia-denies"} & set(r.set))
        self.assertTrue(any(u.startswith("Conflicting") for u in r.unclear))
        r = story.read_notes("No hearing loss he thinks but wife says he turns the TV way up.")
        self.assertFalse({"hearingloss", "hearingloss-denies"} & set(r.set))

    def test_a_habit_with_its_own_amount_after_a_denial(self):
        self.assertTrue(story.read_notes("Doesn't smoke, wine on weekends.").set.get("social-etoh"))
        self.assertTrue(story.read_notes("Never smoked, occasional ETOH.").set.get("social-etoh"))
        self.assertNotIn("social-etoh", story.read_notes("Denies smoking, alcohol.").set)
        self.assertNotIn("social-tobacco", story.read_notes("Pt states he does not smoke, but intake form lists 1 ppd.").set)
        r = story.read_notes("Remote ETOH abuse, sober 15 yrs.")
        self.assertNotIn("social-etoh", r.set)
        self.assertTrue(r.set.get("social-etoh-remote"))

    def test_non_words_never_tick_their_positive_box(self):
        r = story.read_notes("Non-pulsatile tinnitus AS.")
        self.assertTrue(r.set["tinnitus"] and r.set["t-nonpulsatile"] and r.set["t-left"])
        self.assertNotIn("t-pulsatile", r.set)
        self.assertNotIn("assoc-vertigo", story.read_notes("Dizziness, non-spinning, worse standing.").set)
        self.assertNotIn("h-course-throb", story.read_notes("Headaches, non-throbbing, frontal.").set)

    def test_daily_is_how_often_not_a_course(self):
        self.assertNotIn("course-chronic", story.read_notes("Dizziness daily, worse standing.").set)
        self.assertNotIn("h-course-chronic", story.read_notes("Daily headaches, every day in the morning.").set)
        self.assertTrue(story.read_notes("Constant dizziness.").set.get("course-chronic"))

    def test_someone_elses_symptoms(self):
        self.assertEqual(ticked(story.read_notes("Dad had hearing aids. Mother has migraines. Sister w/ tinnitus.")), set())
        r = story.read_notes("Hx migraines since teens, mom also has migraines.")
        self.assertTrue(r.set.get("hist-migraine"))                    # the patient's own history, before "mom"
        self.assertNotIn("headache", r.set)

    def test_what_is_over_is_not_current(self):
        r = story.read_notes("Had vertigo years ago that resolved.")
        self.assertFalse({"dizziness", "assoc-vertigo"} & set(r.set))
        self.assertTrue(any(u.startswith("Past") for u in r.unclear))
        self.assertNotIn("headache", story.read_notes("Used to get migraines.").set)
        r = story.read_notes("Constant rocking feeling x 8 months, ever since a bad spinning episode last winter (that one resolved in a few days).")
        self.assertTrue(r.set.get("dizziness") and r.set.get("assoc-diseq"))
        self.assertFalse({"assoc-vertigo", "duration-days"} & set(r.set))
        self.assertNotIn("hist-hypt", story.read_notes("I had high blood pressure with my last pregnancy but it went away.").set)

    def test_shorthand_the_wording_writes_is_read_the_same(self):
        for raw in (DIZZY, HEADACHE, NUMBNESS, TREMOR, MEMORY):
            self.assertEqual(ticked(story.read_notes(raw)), ticked(story.read_notes(wording.clinical(raw))), raw[:30])
        r = story.read_notes("- HA. Tinnitus AS, w/o hearing loss.")
        self.assertTrue(r.set["headache-denies"] and r.set["t-left"])
        self.assertNotIn("headache", ticked(r))

    def test_a_cut_off_clause_ticks_nothing(self):
        self.assertNotIn("duration-seconds", story.read_notes("Dizzy. Symptoms last seconds to...").set)
        self.assertNotIn("social-tobacco", story.read_notes("3-4 beers most nights, vapes...").set)
        self.assertTrue(story.read_notes("Mild hearing loss both ears. HTN...").set.get("hist-hypt"))   # complete words before the "..."

    def test_carried_forward_without_a_birth_date(self):
        nxt = note.carry_forward({"Name": "Sample, Bob", "HPI-age": "70", "hpi-male": True, "Date": "06/01/2025"})
        self.assertEqual(nxt["HPI-age"], "")                          # last visit's age is stale
        self.assertIn("Mr. Sample is a *** y/o", nxt["ChiefComplaint-descr"])


def _build(values, generated):
    return compose.compose_note(values, {k: list(v) for k, v in generated.items()})


def _apply(values, generated, out, only=None):
    values = dict(values)
    for p in out.proposals:
        if only is None or p.id in only:
            values.update(p.changes)
            if p.field:
                generated[p.field] = p.generated
    return values


class DraftedHpiTests(unittest.TestCase):
    """The HPI is drafted from the boxes, the notes' other words kept in the clinic's wording, and a rebuild is stable."""

    def start(self, text, **extra):
        v = note.blank()
        v.update({"Date": "10/06/2026", "neuro-consult": True, **extra})
        v["ChiefComplaint-descr"] = v["ChiefComplaint-descr"].replace("\r", "\n") + "\n" + text
        return v

    def settle(self, v, generated, only=None):
        for _ in range(4):
            out = _build(v, generated)
            if not any(p.id == "hpi" for p in out.proposals):
                return v, out
            v = _apply(v, generated, out, only)
        self.fail("the HPI keeps changing on rebuild")

    def test_notes_become_paragraphs_and_the_rest_of_the_words(self):
        gen = {}
        v = self.start(DIZZY)
        v = _apply(v, gen, _build(v, gen))                            # boxes and slots from the notes, and the HPI
        out = _build(v, gen)
        hpi = next(p for p in out.proposals if p.id == "hpi")
        paras = hpi.text.split("\n\n")
        self.assertTrue(paras[0].startswith("Mr./Ms. **** is a 73 y/o ***HD *** presenting to the clinic today for a consultation on dizziness."))
        # a paragraph per complaint, the chief complaint's first
        self.assertEqual(paras[1], "Pt reports dizziness w/ vertigo. Described as lightheadedness w/ room moving at times. Onset possibly January.")
        self.assertEqual(paras[2], "Reports tinnitus AS.")
        self.assertNotIn("Described as lightheaded but also", hpi.text)  # the notes' words the boxes and slots say are not repeated
        self.assertNotIn("Started possibly January", hpi.text)
        self.assertIn("More frequent this month.", paras[3])           # what they don't say stays, in the clinic's wording
        self.assertIn("Worse standing up and when turning head quickly.", paras[3])
        self.assertNotIn("worse w/ standing", paras[1])                # ... and the boxes those words say aren't written twice
        self.assertNotIn("worsening", paras[1])
        self.assertIn("Possible hearing loss.", paras[3])              # the hedge kept; "has ringing AS" is in the tinnitus line
        self.assertIn("BP medication changed recently but uncertain which one.", paras[3])
        self.assertTrue(paras[3].endswith("Sxs last seconds to ***"))
        self.assertEqual(hpi.generated, [paras[0], paras[1], paras[2]])  # the provider's sentences are theirs, not the builder's
        self.assertEqual([p["key"] for p in hpi.parts][:3], ["opening", "dizziness", "tinnitus"])
        v, out = self.settle(_apply(v, gen, out), gen)
        self.assertFalse(any(p.id == "hpi" for p in out.proposals))

    def test_applying_only_the_hpi_is_stable(self):
        for text in (DIZZY, HEADACHE, TREMOR, NUMBNESS, MEMORY):
            gen = {}
            v = self.start(text)
            first = next(p for p in _build(v, gen).proposals if p.id == "hpi").text
            v, _out = self.settle(v, gen, only={"hpi"})
            self.assertEqual(v["ChiefComplaint-descr"], first, text[:30])

    def test_typing_more_only_adds_it(self):
        gen = {}
        v, _out = self.settle(_apply(self.start(HEADACHE), gen, _build(self.start(HEADACHE), gen)), gen)
        before = [ln for ln in v["ChiefComplaint-descr"].split("\n") if ln.strip()]
        v["ChiefComplaint-descr"] += "\ndenies vision loss, no fever"
        hpi = next(p for p in _build(v, gen).proposals if p.id == "hpi")
        after = [ln for ln in hpi.text.split("\n") if ln.strip()]
        self.assertEqual([ln for ln in before if ln not in after], [])
        self.assertEqual([ln for ln in after if ln not in before], ["Denies vision loss, no fever."])

    def test_a_denial_is_never_split_and_a_hedge_never_dropped(self):
        v = self.start("Dizzy, denies vertigo, nausea. Room spinning, maybe hearing loss.", dizziness=True, **{"assoc-vertigo": True})
        hpi = next(p for p in _build(v, {}).proposals if p.id == "hpi").text
        self.assertIn("Denies vertigo, nausea.", hpi)
        self.assertNotIn("Nausea.", hpi)
        self.assertIn("possible hearing loss", hpi.lower())

    def test_words_the_boxes_dont_cover_stay(self):
        v = self.start("Denies headache w/ the attacks. Sumatriptan helps the headache but not the dizzy.",
                       dizziness=True, headache=True, **{"headache-denies": False, "assoc-attacks": True})
        hpi = next(p for p in _build(v, {}).proposals if p.id == "hpi").text
        self.assertIn("Denies headache w/ the attacks.", hpi)          # not "Denies headaches.": the attacks matter
        self.assertIn("Sumatriptan helps the headache but not the dizzy.", hpi)
        r = story.read_notes("Dizzy spells. Sumatriptan helps the headache but not the dizzy.")
        self.assertTrue(r.set["dizziness"])                           # "but not the dizzy" isn't a denial
        self.assertFalse(r.set.get("dizziness-denies"))


class TranscriptTests(unittest.TestCase):
    VISIT = ("Doctor: Hi, what brings you in today?\n"
             "Patient: I've been dizzy for about two months. It's like the room is spinning when I roll over in bed.\n"
             "Doctor: How long does it last?\nPatient: Maybe 30 seconds.\n"
             "Doctor: Any ringing in your ears or hearing loss?\nPatient: No.\n"
             "Daughter: She fell last week in the bathroom though.\nPatient: That was because the floor was wet.\n"
             "Doctor: Do you get headaches?\nPatient: Not really. I take meclizine sometimes and it helps a little.\n"
             "Doctor: So you've had spinning with rolling over, lasting under a minute. I'm going to do a Dix-Hallpike.")

    def test_a_pasted_conversation_is_recognised(self):
        self.assertTrue(story.looks_like_visit(self.VISIT))
        self.assertFalse(story.looks_like_visit(DIZZY))

    def test_answers_and_the_patients_words_become_boxes_with_their_source(self):
        r = scribe.visit_reading(self.VISIT, {})
        items = {i["text"]: i for p in r["proposals"] for i in p["items"]}
        for want in ("Dizziness: present", "Dizziness: with vertigo", "Dizziness: worse with lying supine in bed", "Dizziness: lasts seconds",
                     "Tinnitus: patient denies", "Hearing loss: patient denies", "Headache: patient denies", "Falls: yes"):
            self.assertIn(want, items)
        self.assertEqual(items["Tinnitus: patient denies"]["why"], "Any ringing in your ears or hearing loss? — No.")
        self.assertIn("Meclizine", r["meds"])
        self.assertFalse(r["unclear"])
        self.assertIn("I've been dizzy for about two months.", r["story"])
        self.assertIn("ChiefComplaint-descr", r["append"])
        self.assertIn("imaging", r)
        self.assertIn("studies", r)

    def test_numbered_speakers_are_told_apart(self):
        t = ("Speaker 1: Do you get dizzy?\nSpeaker 2: Yes, when I stand up.\nSpeaker 1: Any ringing in your ears?\nSpeaker 2: No.\n"
             "Speaker 1: Any falls?\nSpeaker 2: No, none.")
        r = scribe.read_visit(t)
        self.assertTrue(r.set["dizziness"])
        self.assertTrue(r.set["tinnitus-denies"])
        self.assertTrue(r.set["ga-falls-no"])

    def test_the_providers_plans_are_not_history(self):
        t = ("I'm going to check if you get dizzy when I tilt your head back. Let's see whether the room spins. "
             "I think this could be BPPV.")
        r = scribe.read_visit(t)
        self.assertNotIn("dizziness", r.set)


class MedicationListTests(unittest.TestCase):
    TEXT = ("--------PO Medications--------\nAmlodipine 5mg QD\nGabapentin 300mg TID (new)\nTopamax 50mg BID (D/C)\n"
            "Meclizine 25 mg PRN dizziness (not taking)\nLipo-Flavonoid 1 cap TID\n"
            "***I, SW, independently reviewed the medication list with patient, 10/06/2026***")

    def test_the_box_reads_as_rows(self):
        rows = medlist.parse(self.TEXT)
        self.assertEqual([r["kind"] for r in rows], ["header", "med", "med", "med", "med", "med", "review"])
        self.assertEqual(medlist.split_line("Gabapentin 300mg TID (new)"), {"name": "Gabapentin", "dose": "300mg", "sig": "TID", "status": "new"})
        self.assertEqual(medlist.split_line("Lipo-Flavonoid 1 cap TID")["dose"], "1 cap")
        self.assertEqual(medlist.split_line("Topamax 50mg BID (D/C)")["status"], "stopped")
        self.assertEqual(medlist.split_line("Topamax 50mg BID (did see)")["status"], "stopped")
        self.assertEqual(medlist.split_line("Topamax 50mg BID (dee cee'd)")["status"], "stopped")

    def test_the_next_visit_reviews_the_list_again(self):
        carried = medlist.carry(self.TEXT)
        self.assertNotIn("Topamax", carried)                           # stopped last visit
        self.assertIn("Gabapentin 300mg TID", carried)
        self.assertNotIn("(new)", carried)
        self.assertNotIn("reviewed the medication list", carried)
        self.assertIn("(not taking)", carried)                         # still worth asking about
        nxt = note.carry_forward({"meds-descr": self.TEXT, "Name": "Sample, Alice"})
        self.assertEqual(nxt["meds-descr"], carried)

    def test_build_asks_for_the_review_until_it_is_done(self):
        v = {"Date": "10/06/2026", "meds-descr": "Amlodipine 5mg QD"}
        self.assertTrue(any("Medications: go over the list" in t for t in compose.compose_note(v).todo))
        v["meds-descr"] = self.TEXT
        self.assertFalse(any("Medications: go over the list" in t for t in compose.compose_note(v).todo))
        self.assertTrue(medlist.reviewed_today(v))

    def test_medications_named_in_the_story_are_raised(self):
        out = compose.compose_note({"ChiefComplaint-descr": HEADACHE, "meds-descr": "Amlodipine 5mg QD"})
        self.assertTrue(any("Ibuprofen" in t and "medication list" in t for t in out.todo))

    def test_the_drug_list(self):
        drugs = medlist.drugs()
        self.assertGreater(len(drugs), 500)
        names = [d["name"].lower() for d in drugs]
        self.assertEqual(len(names), len(set(names)))
        for d in drugs:
            self.assertTrue({"name", "brands", "class", "strengths", "sigs", "ototoxic", "vestibular_suppressant"} <= set(d), d["name"])
            self.assertNotIn("(", d["name"])
        oto = {d["name"] for d in drugs if d["ototoxic"]}
        self.assertTrue({"Gentamicin", "Cisplatin", "Furosemide"} <= oto)
        self.assertFalse({"Ibuprofen", "Aspirin", "Sildenafil"} & oto)    # only what a hearing clinic means by ototoxic
        self.assertTrue(medlist.lookup("Meclizine")["vestibular_suppressant"])
        self.assertEqual(medlist.lookup("Nurtec")["name"], "Rimegepant")
        self.assertEqual(medlist.search("topa")[0]["name"], "Topiramate")
        self.assertEqual(story.find_meds("Caffeine 3-4 cups, took Advil."), ["Advil"])


class ScreenFilesTests(unittest.TestCase):
    WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recordfinder", "charting", "web")

    def test_meds_and_dates_are_on_the_note(self):
        with open(os.path.join(self.WEB, "modules", "note.js"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('["meds", "Medications", 1]', src)
        self.assertIn("dateField(\"DOB\", { span: 3, dob: true })", src)
        self.assertIn("ageField(", src)
        self.assertNotIn('textField("HPI-age"', src)                    # age isn't typed
        self.assertIn("looksLikeScope(t) ? \"scope\"", src)                 # a pasted Scope note is recognised, not chosen
        self.assertIn('pr.requires_confirmation', src)
        with open(os.path.join(self.WEB, "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertLess(html.index("modules/datepick.js"), html.index("modules/note.js"))
        self.assertLess(html.index("modules/meds.js"), html.index("modules/note.js"))
        with open(os.path.join(self.WEB, "meds.json"), encoding="utf-8") as f:
            self.assertIsInstance(json.load(f), list)

    def test_past_studies_open_from_the_note(self):
        with open(os.path.join(self.WEB, "modules", "note.js"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"Reports on file"', src)
        self.assertIn('STUDY_GROUPS = ["Neuro testing", "Vestibular testing"]', src)
        self.assertIn("closeReport();", src.split("function putAway() {")[1][:200])   # never left open over another patient
        with open(os.path.join(self.WEB, "modules", "review.js"), encoding="utf-8") as f:
            self.assertIn("show: showDoc", f.read())

    def test_no_function_is_declared_twice_in_one_scope(self):
        # a second `function add` in meds.js silently replaced the search box's add(), so picking a drug did nothing
        import glob, re, collections
        files = glob.glob(os.path.join(self.WEB, "modules", "*.js")) + [os.path.join(self.WEB, "shell.js")]
        for path in files:
            seen = collections.defaultdict(list)
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    m = re.match(r"^(\s*)(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", line)
                    if m:
                        seen[(len(m.group(1)), m.group(2))].append(i)
            twice = {name: lines for (_, name), lines in seen.items() if len(lines) > 1}
            self.assertEqual(twice, {}, os.path.basename(path))

    def test_mentioned_drugs_can_be_added_from_build(self):
        with open(os.path.join(self.WEB, "modules", "meds.js"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("markReviewed, addMentioned, names", src)
        self.assertIn('[/^(?:as needed|when needed|prn)$/, "PRN"]', src)
        with open(os.path.join(self.WEB, "modules", "note.js"), encoding="utf-8") as f:
            self.assertIn("meds().addMentioned(n, context)", f.read())

    def test_cn8_ear_rows_render_with_the_sided_row(self):
        # CN VIII became a Normal/Right/Left/Both row; its Ear canals / Weber / Rinne rows must still follow it
        with open(os.path.join(self.WEB, "modules", "note.js"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('table.append(sidedRow(row)); if (row.normal === "normal27") table.append(...earRows()); continue;', src)
        for field in ('selectField("weber"', 'selectField("rinne-ab"', 'selectField("rinne-ba"', 'check("debris-kerat")', 'check("debris-cerumen")'):
            self.assertIn(field, src.split("const earRows = () => [")[1].split("];")[0])

    def test_demo_patient_has_an_emg_report_to_open(self):
        import tempfile
        from recordfinder.charting.providers import DemoProvider
        with tempfile.TemporaryDirectory() as root:
            docs = DemoProvider(root).chart({"id": 3})["documents"]
            emg = [d for d in docs if d["kind"] == "EMG/NCV"]
            self.assertEqual(len(emg), 1)
            self.assertEqual(emg[0]["group"], "Neuro testing")
            self.assertTrue(os.path.isfile(emg[0]["path"]))


if __name__ == "__main__":
    unittest.main()


def on(r):
    """The boxes the reading ticks (True) and the text boxes it fills."""
    return {k for k, v in r.set.items() if v is True or (isinstance(v, str) and v)}


class ReviewedReaderTests(unittest.TestCase):
    """Inputs reviewers found the typed-notes reader getting wrong. Each asserts what the words mean."""

    def test_a_not_that_leans_on_the_clause_before_is_not_a_denial(self):
        r = story.read_notes("sumatriptan helps the headache not the dizziness.")
        self.assertNotIn("dizziness-denies", on(r))
        r = story.read_notes("38 y/o female hx of migraines since teens, now episodes of dizziness w/ headache, sometimes without headache, "
                             "bothered by lights and noise during. lasts hours to a day. worse with screens and grocery stores. no hearing "
                             "loss but ears feel full during episodes? sumatriptan helps the headache not the dizziness. caffeine 2 cups")
        self.assertTrue({"dizziness", "worse-opto", "duration-hours", "duration-days"} <= on(r))
        self.assertFalse({"dizziness-denies", "headache", "h-worse-lights"} & on(r))     # screens: a trigger of the dizziness

    def test_a_history_of_migraine_is_not_a_current_headache(self):
        r = story.read_notes("hx of migraines, dizziness w/o headache.")
        self.assertTrue({"hist-migraine", "dizziness", "headache-denies"} <= on(r))
        self.assertNotIn("headache", on(r))
        self.assertNotIn("dizziness-denies", on(story.read_notes("hx of migraines, now episodes of dizziness, sometimes without headache.")))

    def test_over_and_done_with(self):
        for text in ("mild spinning first day, now resolved.", "Sudden HL AD noticed on waking 5 days ago, +tinnitus AD, mild vertigo first day only.",
                     "No dizziness for the past 2 weeks after starting meclizine; before that daily vertigo."):
            with self.subTest(text=text):
                r = story.read_notes(text)
                self.assertFalse({"assoc-vertigo", "DizzFreq"} & on(r))
                self.assertTrue(any(u.startswith("Past") for u in r.unclear))
        r = story.read_notes("Dizziness for the past year it never went away")
        self.assertIn("dizziness", on(r))                                # "never went away" is still there
        self.assertFalse(any(u.startswith("Past") for u in r.unclear))

    def test_a_companion_the_patient_disputes_after_a_comma(self):
        for text, subject in (("wife says he's been more unsteady, he says he's fine.", "gaitataxia"),
                              ("Pt c/o dizziness x 1 yr. Wife reports pt has been falling, pt denies falls.", "ga-falls-yes"),
                              ("Wife reports he has hearing loss, he disagrees.", "hearingloss"),
                              ("Wife says he's unsteady. He says he's fine.", "gaitataxia")):
            with self.subTest(text=text):
                r = story.read_notes(text)
                self.assertNotIn(subject, on(r))
                self.assertTrue(any(u.startswith("Conflicting") for u in r.unclear))

    def test_a_kind_of_dizziness_denied_is_not_dizziness_denied(self):
        r = story.read_notes("No vertigo, more lightheaded.")
        self.assertIn("dizziness", on(r))
        self.assertNotIn("dizziness-denies", on(r))
        r = story.read_notes("Denies vertigo but endorses lightheadedness w/ standing. Not sure if hearing worse.")
        self.assertTrue({"dizziness", "worse-standing"} <= on(r))
        self.assertFalse(any(u.startswith("Conflicting") for u in r.unclear))
        self.assertEqual(len([u for u in r.unclear if "hearing worse" in u]), 1)          # one line for one doubt

    def test_details_belong_to_the_symptom_they_are_said_about(self):
        r = story.read_notes("62yo F says left ear feels plugged and ringing gets louder before the spells, spells last like two to three "
                             "hours, room spinning, vomits sometimes.")
        self.assertIn("duration-hours", on(r))                           # the spells are the dizziness
        self.assertFalse({"t-dur-hours", "t-course-parox"} & on(r))
        r = story.read_notes("s/p MVC 3 wks ago, rear-ended, +whiplash, no LOC. Since then intermittent dizziness w/ head turns, HA daily, "
                             "trouble concentrating.")
        self.assertIn("course-parox", on(r))
        self.assertFalse({"DizzFreq", "h-course-parox"} & on(r))         # daily is the headache's, intermittent the dizziness's
        for text in ("c/o dizziness x 6 wks, lightheaded, worse w/ standing. ASA 81 mg daily, metoprolol 25 mg BID. No syncope.",
                     "Episodic vertigo w/ L ear fullness and roaring tinnitus, fluctuating hearing AS. Attacks last 3-6 hrs, last one 2 wks ago. "
                     "Low-salt diet, HCTZ 25 mg daily. Neg for HA."):
            self.assertNotIn("DizzFreq", on(story.read_notes(text)))     # the dose's "daily"
        r = story.read_notes("Hx migraine since teens. Episodes of vertigo lasting 2-4 hrs, 1-2x/mo, w/ photophobia and phonophobia, +HA in "
                             "~half. Triggers: poor sleep, red wine.")
        self.assertEqual(r.set.get("DizzFreq"), "1-2x/month")
        self.assertFalse({"h-freq-month", "h-assoc-photo", "social-etoh"} & on(r))

    def test_a_denial_scoped_to_the_attacks_is_not_a_denial_of_headaches(self):
        r = story.read_notes("Dizzy spells. denies headache with them.")
        self.assertNotIn("headache-denies", on(r))

    def test_onsets_said_two_ways(self):
        r = story.read_notes("says dizziness started about two weeks ago or maybe three, after a cold.")
        self.assertEqual(r.set["DizzOnsetDate"], "~2 or possibly 3 weeks ago")
        for text in ("Dizzy, onset 2 weeks ago, possibly 2 months.", "Dizzy x3 weeks, though husband thinks longer."):
            r = story.read_notes(text)
            self.assertFalse({"DizzOnsetDate", "OnsetDate"} & set(r.set), text)
            self.assertTrue(any("Onset unclear" in u for u in r.unclear))

    def test_the_patient_as_the_family_calls_them(self):
        self.assertTrue({"dizziness", "worse-standing"} <= on(story.read_notes("Daughter states mom gets dizzy when she stands up fast, mom says only sometimes.")))

    def test_a_denial_taken_back(self):
        r = story.read_notes("no tinnitus. well, maybe a little on the left")
        self.assertFalse({"tinnitus", "tinnitus-denies"} & on(r))
        self.assertTrue(any("both ways" in u for u in r.unclear))

    def test_hearing_aids_are_not_hearing_loss_said(self):
        r = story.read_notes("no vertigo, no hearing loss, but has tinnitus both ears for years, steady, worse at night when quiet. hearing aids help a bit")
        self.assertIn("t-better-hearingaids", on(r))
        self.assertNotIn("hlbetter-hearingaids", on(r))
        r = story.read_notes("68 yo F with dizziness x 6 weeks. No hearing aids. No tinnitus meds have helped. No headache relief with Tylenol.")
        self.assertFalse({"hearingloss-denies", "tinnitus-denies", "headache-denies"} & on(r))

    def test_no_details_for_a_symptom_said_both_ways(self):
        r = story.read_notes("Denies tinnitus. Later says she has a whooshing in her right ear when lying down at night.")
        self.assertFalse({"t-pulsatile", "t-right", "tinnitus-descr"} & on(r))

    def test_more_or_maybe_more_is_a_complete_thought(self):
        r = story.read_notes("vertigo maybe twice a week, maybe more")
        self.assertEqual(r.set["DizzFreq"], "possibly 2x/week, possibly more")
        self.assertEqual(r.unfinished, "")

    def test_neg_and_neg_for_are_denials(self):
        r = story.read_notes("c/o lightheadedness x1 mo. Neg for vertigo, hearing loss, tinnitus, aural fullness, HA, visual changes, focal "
                             "weakness, numbness/tingling. No falls.")
        self.assertTrue({"hearingloss-denies", "tinnitus-denies", "headache-denies", "ga-falls-no", "dizziness"} <= on(r))
        self.assertFalse({"hearingloss", "tinnitus", "headache", "assoc-vertigo", "assoc-tinnitus"} & on(r))
        r = story.read_notes("Episodic vertigo x 2 mo, lasts seconds, w/ head turns to L. Neg n/v. Neg HL/tinnitus. Neg HA.")
        self.assertTrue({"hearingloss-denies", "tinnitus-denies", "headache-denies"} <= on(r))

    def test_the_other_ear_being_fine_is_not_a_side(self):
        r = story.read_notes("R ear fullness and decreased hearing AD x1 wk after flight. No otalgia, no drainage. L ear normal.")
        self.assertIn("hl-right", on(r))
        self.assertNotIn("hl-left", on(r))
        self.assertNotIn("t-left", on(story.read_notes("c/o tinnitus R ear x 1 yr. L ear fine. No HL.")))

    def test_the_clinics_hl_shorthand(self):
        r = story.read_notes("Sudden HL AD noticed on waking 5 days ago, +tinnitus AD.")
        self.assertTrue({"hearingloss", "hl-right"} <= on(r))
        self.assertIn("hearingloss-denies", on(story.read_notes("c/o tinnitus R ear x 1 yr. No HL.")))

    def test_constant_for_two_weeks_is_not_chronic(self):
        r = story.read_notes("Never had vertigo before this. Dizziness started 2 wks ago after a cold, constant, worse w/ head movement.")
        self.assertNotIn("course-chronic", on(r))
        self.assertTrue(story.read_notes("Constant dizziness x 8 months.").set.get("course-chronic"))

    def test_an_event_isnt_the_complaint_it_follows(self):
        r = story.read_notes("F/u for BPPV. s/p Epley R x2 at last visit. Since then 1 brief episode turning head, much improved. No falls. "
                             "Still mild imbalance on stairs. Stopped meclizine.", topics=["gait"])
        self.assertNotIn("ga-course-improving", on(r))
        self.assertNotIn("Meclizine", r.meds)                            # stopped: to review, not to add to the list
        self.assertTrue(any("meclizine" in m.lower() for m in r.med_notes))
        r = story.read_notes("c/o imbalance. Fell 2x last month, once tripped over dog, once lightheaded getting up at night to urinate.")
        self.assertNotIn("dizziness", on(r))

    def test_two_negatives(self):
        for text in ("70 yo F with imbalance. Never free of the ringing.", "Not a day goes by without dizziness.",
                     "Hasn't been without tinnitus since 2019."):
            r = story.read_notes(text)
            self.assertFalse({"tinnitus-denies", "dizziness-denies"} & on(r), text)
        self.assertNotIn("gaitataxia-denies", on(story.read_notes("Doesn't go anywhere without her cane due to imbalance.")))

    def test_a_denial_of_a_change_is_not_a_denial(self):
        for text in ("Never had hearing tested.", "No improvement in the dizziness since last visit.",
                     "No new headaches; the old ones continue weekly.", "Follow up for BPPV. No new hearing loss since last visit.",
                     "Dizziness x 1 yr. No dizziness today."):
            r = story.read_notes(text)
            self.assertFalse({"hearingloss-denies", "dizziness-denies", "headache-denies"} & on(r), text)
        r = story.read_notes("Dizziness x 1 yr. No dizziness today.")
        self.assertIn("dizziness", on(r))
        self.assertFalse(r.unclear)
        r = story.read_notes("c/o constant tinnitus AS x6 mo, high-pitched, worse at night. +/- hearing loss AS, pt unsure.")
        self.assertEqual(r.unclear, ["Hearing loss is uncertain: \u201c+/- hearing loss AS, pt unsure.\u201d"])   # one line per doubt

    def test_ruled_out_and_differentials_are_not_symptoms(self):
        self.assertNotIn("hearingloss", on(story.read_notes("Dizzy when turning in bed. Hearing loss AS ruled out by audiogram.")))
        for text in ("R/O vestibular migraine.", "48 yo F referred for ? vestibular migraine vs BPPV.", "Dx with migraine vs Menieres by ENT."):
            self.assertNotIn("headache", on(story.read_notes(text)), text)

    def test_a_side_goes_with_the_symptom_it_is_said_with(self):
        for text in ("59 yo M with ringing in the left ear, hearing loss in the right ear since a work injury.", "Ringing AS, hearing loss AD."):
            r = story.read_notes(text)
            self.assertTrue({"t-left", "hl-right"} <= on(r), text)
            self.assertFalse({"t-right", "hl-left", "hlassoc-tinnitus", "t-assoc-hearingloss"} & on(r), text)
        self.assertNotIn("hl-right", on(story.read_notes("Hearing loss, hx of AD per daughter.")))
        for text in ("Ringing in the left ear, maybe both.", "Ringing in the left ear, I think.", "Hearing loss AD, ? AS too."):
            r = story.read_notes(text)
            self.assertFalse({"t-left", "t-bilat", "hl-bilat", "hl-left"} & on(r), text)

    def test_an_exception_is_not_a_denial(self):
        r = story.read_notes("No weakness except in her right hand.")
        self.assertNotIn("ga-assoc-weakness-denies", on(r))

    def test_habits_that_are_not_the_patients(self):
        for text in ("Non-smoker.", "Lifelong non smoker, drinks wine socially.", "Wife smokes."):
            self.assertNotIn("social-tobacco", on(story.read_notes(text)), text)

    def test_rare_and_once_are_listed_not_ticked(self):
        r = story.read_notes("66 yo M with dizziness for 3 months. Occasionally worse with standing. Rarely worse with walking. Only once was it spinning.")
        self.assertFalse({"worse-walking", "assoc-vertigo"} & on(r))

    def test_a_question_and_its_answer(self):
        r = story.read_notes("Headache? Denies.")
        self.assertIn("headache-denies", on(r))
        r = story.read_notes("Tinnitus? yes, AS.")
        self.assertTrue({"tinnitus", "t-left"} <= on(r))

    def test_drug_names_that_are_words(self):
        meds = story.find_meds("Daughter Heather says he drifts to the left. Takes Soma for back pain. Eyes don't align.")
        self.assertEqual(meds, ["Soma"])

    def test_the_same_hedge_raw_or_rewritten(self):
        raw, written = story.read_notes("Dizzy. lasts maybe thirty seconds"), story.read_notes("Dizzy. lasts possibly 30 seconds")
        self.assertEqual(on(raw), on(written))

    def test_imbalance_that_describes_the_dizziness(self):
        r = story.read_notes("Dizziness for two years, no longer spinning, now more of a constant imbalance, getting better with therapy.")
        self.assertIn("assoc-diseq", on(r))
        self.assertFalse({"gaitataxia", "assoc-vertigo"} & on(r))


def build_until_stable(text, **extra):
    v = note.blank()
    v.update({"Date": "10/06/2026", "neuro-consult": True, **extra})
    v["ChiefComplaint-descr"] = text
    gen, out = {}, None
    for _ in range(5):
        out = compose.compose_note(v, {k: list(x) for k, x in gen.items()})
        if not any(p.id == "hpi" for p in out.proposals) and _:
            return v, out
        for p in out.proposals:
            if p.id not in ("exam", "impression", "treatment"):
                v.update(p.changes)
                if p.field:
                    gen[p.field] = p.generated
    raise AssertionError("the HPI keeps changing on rebuild")


class ReviewedHpiTests(unittest.TestCase):
    """Drafted HPIs reviewers found redundant or changed in meaning. Each is built, applied and rebuilt until stable."""

    def hpi(self, text, **extra):
        v, _out = build_until_stable(text, **extra)
        return v["ChiefComplaint-descr"]

    def test_the_vestibular_migraine_note_stays_a_dizziness_note(self):
        hpi = self.hpi("38 y/o female hx of migraines since teens, now episodes of dizziness w/ headache, sometimes without headache, "
                       "bothered by lights and noise during. lasts hours to a day. worse with screens and grocery stores. no hearing loss but "
                       "ears feel full during episodes? sumatriptan helps the headache not the dizziness. caffeine 2 cups")
        self.assertIn("consultation on dizziness", hpi)
        self.assertIn("lasting hours to days", hpi)                     # "to a day" isn't lost
        self.assertIn("screens and grocery stores", hpi)
        self.assertNotIn("Reports paroxysmal headaches", hpi)

    def test_resolved_vertigo_is_not_drafted_as_current(self):
        hpi = self.hpi("Hearing loss right side sudden 5 days ago woke up with it, fullness and ringing on right, mild spinning first day now "
                       "resolved. was given prednisone 60 mg by urgent care, on day 3.")
        self.assertNotIn("reports vertigo", hpi.lower())
        self.assertIn("now resolved", hpi)

    def test_a_walker_is_not_the_reason_for_the_visit(self):
        v, out = build_until_stable("84 yo M w walker, wife says he's been more unsteady past 6 months and shuffling, he says he's fine. two "
                                    "falls this year, one at night going to bathroom.")
        self.assertNotIn("consultation on walker", v["ChiefComplaint-descr"])
        self.assertFalse(v["ChiefComplaint"].lower().startswith("walker"))
        self.assertIn("Mr. **** is an 84 y/o", v["ChiefComplaint-descr"])
        self.assertEqual(compose.dictated_opening({}, wording.clinical("84 y/o M w/ hx of falls, here for dizziness"))[1]["reason"], "dizziness")

    def test_a_link_in_time_is_kept(self):
        hpi = self.hpi("dizzy when she stands up, lasts a few seconds, then fine. no spinning. not when lying down. BP meds - lisinopril - "
                       "started 2 months ago, around when dizziness started")
        self.assertIn("around when dizziness started", hpi)
        self.assertNotIn("\nFine.", hpi)
        self.assertNotIn(" Fine.", hpi)

    def test_negatives_say_only_what_was_denied(self):
        hpi = self.hpi("woke up and rolled over to the right and room was spinning, lasts less than a minute, maybe 20 seconds. no hearing "
                       "loss no ringing. no falls")
        self.assertNotIn("fluctuating", hpi)
        self.assertNotIn("aural fullness", hpi)
        hpi = self.hpi("68 y/o F c/o dizziness x3 mo, room spinning when rolling over in bed to the R, lasts <1 min. +nausea, neg vomiting. "
                       "Denies hearing loss, tinnitus, aural fullness. Hx HTN, DM2. Meclizine 25 mg prn w/ minimal relief.")
        self.assertIn("Denies hearing loss, tinnitus, aural fullness.", hpi)
        self.assertNotIn("fluctuating", hpi)
        self.assertIn("PMHx significant for HTN and DM2.", hpi)
        self.assertNotIn("Hx HTN, DM2", hpi)                            # said once, in the opening

    def test_the_provider_s_words_and_the_paragraph_dont_both_say_it(self):
        hpi = self.hpi("Vertigo attacks 2x a month lasting 20 min to 1 hr, with right ear fullness and low tone hearing loss on right, tinnitus "
                       "right ear roaring. No headache. Not taking anything.")
        self.assertIn("Vertigo attacks 2x/month lasting 20 min to 1 hr", hpi)
        self.assertNotIn("Occurs 2x/month", hpi)
        self.assertNotIn("Occurs in attacks", hpi)
        self.assertNotIn("Pt reports vertigo", hpi)
        hpi = self.hpi("c/o constant tinnitus AS x6 mo, high-pitched, worse at night. +/- hearing loss AS, pt unsure.")
        self.assertNotIn("6 months ago", hpi)
        self.assertEqual(hpi.count("tinnitus AS"), 2)                  # the reason for the visit and the provider's words
        hpi = self.hpi("Patient is a 74 year old right handed male who presents with imbalance for about a year. He does have some hearing "
                       "loss in both ears and wears hearing aids.")
        self.assertNotIn("Reports hearing loss AU", hpi)
        self.assertIn("hearing loss AU and wears hearing aids", hpi)
        hpi = self.hpi("Had vertigo 10 years ago that resolved with Epley. Now presents with headaches on the left side.")
        self.assertEqual(hpi.count("left"), 1)
        hpi = self.hpi("Dizzy maybe 2-3 times a week, lasting perhaps 20 minutes or so.")
        self.assertEqual(hpi.count("2-3x/week"), 1)
        hpi = self.hpi("66 yo M with dizziness for 3 months. Occasionally worse with standing. Rarely worse with walking. Only once was it spinning.")
        self.assertIn("Occasionally worse w/ standing.", hpi)
        self.assertNotIn("worse w/ standing and walking", hpi)
        self.assertIn("Pt reports dizziness.", hpi)

    def test_an_association_is_written_once(self):
        v = note.blank()
        v.update({"Date": "10/06/2026", "neuro-consult": True, "dizziness": True, "assoc-vertigo": True, "assoc-tinnitus": True,
                  "assoc-hearingloss": True, "assoc-auralfullness": True, "hearingloss": True, "hl-right": True, "hlassoc-vertigo": True,
                  "hlassoc-tinnitus": True, "hlassoc-aural": True, "tinnitus": True, "t-right": True, "t-assoc-vertigo": True,
                  "t-assoc-hearingloss": True, "t-assoc-aural": True})
        hpi = next(p for p in compose.compose_note(v).proposals if p.id == "hpi").text
        self.assertEqual(hpi.count("aural fullness"), 1)
        self.assertNotIn("Assoc w/ vertigo", hpi)
        self.assertEqual(hpi.count("Assoc w/"), 2)                      # dizziness: tinnitus, hearing loss, fullness; hearing: tinnitus

    def test_the_chief_complaint_comes_first(self):
        v = note.blank()
        v.update({"Date": "10/06/2026", "neuro-consult": True, "ChiefComplaint": "Imbalance", "gaitataxia": True, "hearingloss": True})
        hpi = next(p for p in compose.compose_note(v).proposals if p.id == "hpi").text
        self.assertLess(hpi.index("Reports imbalance"), hpi.index("Reports hearing loss"))

    def test_an_article_before_an_age(self):
        hpi = self.hpi("81 yo F with imbalance x 1 yr, worse walking and turning corners, uses cane.")
        self.assertIn("is an 81 y/o", hpi)
        self.assertNotIn("quickly", hpi)
        self.assertIn("Reports imbalance.", hpi)                       # the patient's word, not "gait ataxia"

    def test_a_paragraph_that_is_only_its_first_line_goes_when_the_notes_say_it(self):
        hpi = self.hpi("Imbalance worse in the dark and on uneven ground, no dizziness. uses a cane outside only. no falls in last year but "
                       "near fall last week.")
        self.assertNotIn("Reports gait ataxia.", hpi)
        self.assertNotIn("Reports imbalance.", hpi)

    def test_lightheaded_is_kept(self):
        for text in ("c/o lightheadedness x 2 mo, worse w/ standing. No falls.",
                     "Lightheaded on standing, not spinning, x4 mo since lisinopril increased to 20 mg. BP 102/64 seated."):
            hpi = self.hpi(text)
            self.assertIn("ightheaded", hpi, text)
            self.assertNotIn("Not spinning, x4", hpi)

    def test_neg_for_lists_stay_whole(self):
        hpi = self.hpi("c/o lightheadedness x1 mo. Neg for vertigo, hearing loss, tinnitus, aural fullness, HA, visual changes, focal weakness, "
                       "numbness/tingling. No falls.")
        self.assertIn("Neg for vertigo, hearing loss, tinnitus, aural fullness, HA, visual changes, focal weakness, numbness/tingling.", hpi)
        self.assertNotIn("Reports tinnitus", hpi)
        self.assertNotIn("Denies headaches", hpi)                       # HA is in the list already

    def test_drug_doses_are_not_how_often(self):
        self.assertNotIn("Occurs daily", self.hpi("c/o dizziness x 6 wks, lightheaded, worse w/ standing. ASA 81 mg daily, metoprolol 25 mg BID."))

    def test_a_follow_up_whose_vertigo_resolved(self):
        v = note.blank()
        v.update({"Date": "10/06/2026", "neuro-progress": True, "ChiefComplaint": "Dizziness", "dizziness": True, "assoc-vertigo": True,
                  "worse-supine": True, "course-parox": True, "DizzFreq": "daily",
                  "ChiefComplaint-descr": "Interim: vertigo resolved after Epley R 8/20. No recurrence. Mild residual imbalance w/ quick turns. No falls."})
        out = compose.compose_note(v, {}, "08/20/2026")
        hpi = next(p for p in out.proposals if p.id == "hpi").text
        self.assertNotIn("Pt reports paroxysmal vertigo", hpi)
        self.assertNotIn("Occurs daily", hpi)
        self.assertTrue(any("over" in c for c in out.check))

    def test_scoped_denials_and_elliptical_halves_stay_whole(self):
        hpi = self.hpi("62yo F says left ear feels plugged and ringing gets louder before the spells, spells last like two to three hours, room "
                       "spinning, vomits sometimes. denies headache with them. not on any diuretic")
        self.assertIn("Denies headache w/ them.", hpi)
        self.assertNotIn("Denies headaches.", hpi)
        self.assertIn("62 y/o", hpi.split("\n")[0])
        hpi = self.hpi("70 yo F with vertigo x 2 months. Worse standing, no worse with walking. Lasts seconds, never hours. Ringing AS, not AD.")
        self.assertNotIn("\nNever hours.", hpi)
        self.assertIn("never hours", hpi)
        self.assertIn("not AD", hpi)
        hpi = self.hpi("Interim: started gabapentin 100 mg TID by PCP for neuropathy, stopped after 1 wk d/t drowsiness. Dizzy spells less "
                       "frequent, now ~1x/wk vs daily before.")
        self.assertIn("Dizzy spells less frequent, now ~1x/wk vs daily before.", hpi)

    def test_two_negatives_are_never_taken_apart(self):
        hpi = self.hpi("70 yo F with imbalance. Never free of the ringing.")
        self.assertIn("Never free of the ringing.", hpi)
        self.assertNotIn("Denies tinnitus", hpi)

    def test_the_reason_for_the_visit(self):
        for text, cc in (("62 yo F presents with no dizziness but new ringing in the right ear.", "New tinnitus AD"),
                         ("70 yo F, dizzy for years. No improvement in the dizziness since last visit.", "Dizziness"),
                         ("seventy two year old male here for imbalance um he says he's been off balance for about a year and it's getting worse",
                          "Imbalance"),
                         ("sixty year old male with headaches about twice a week throbbing behind the left eye with light sensitivity and nausea no aura",
                          "Headaches"),
                         ("F/u for BPPV. s/p Epley R x2 at last visit. Still mild imbalance on stairs.", "BPPV")):
            with self.subTest(text=text):
                v, out = build_until_stable(text)
                self.assertEqual(v["ChiefComplaint"], cc)
                self.assertNotIn("but it is the chief complaint", " ".join(out.check))
        self.assertNotIn("dizziness-denies", on(story.read_notes("Sixty six year old female. She has never had true vertigo, just "
                                                                 "lightheadedness when she stands up.")))
        hpi = self.hpi("Sixty six year old female. She has never had true vertigo, just lightheadedness when she stands up.")
        self.assertNotIn("consultation on she", hpi)
        self.assertIn("She has never had true vertigo, just lightheadedness when she stands up.", hpi)

    def test_age_and_sex_from_the_way_the_notes_start(self):
        for text, lead in (("62yo F says left ear feels plugged.", "Ms. **** is a 62 y/o"),
                           ("so the patient is a 71 year old right handed male who presents with um dizziness for the past year", "Mr. **** is a 71 y/o RHD male"),
                           ("This is a 71-year-old right-handed male who presents for dizziness. He says it started about two months ago.",
                            "Mr. **** is a 71 y/o RHD male presenting to the clinic today for a consultation on dizziness.")):
            hpi = self.hpi(text)
            self.assertTrue(hpi.startswith(lead), hpi)
            self.assertNotIn("71 y/o RHD male who presents", hpi)       # the dictated opening isn't said again below

    def test_openings_from_the_notes_own_first_line(self):
        hpi = self.hpi("43 yo M f/u for R vestibular neuritis, onset 6 wk ago. Interim: constant vertigo resolved after 4 days.")
        self.assertTrue(hpi.startswith("Mr. **** is a 43 y/o ***HD male presenting to the clinic today for a consultation on R vestibular "
                                       "neuritis."), hpi)
        hpi = self.hpi("pt is a 46 year old man, sudden constant spinning started 9 days ago, morning after a cold. couldnt stand.")
        self.assertIn("Onset 9 days ago, morning after a cold.", hpi)   # not a stray "Morning after a cold."

    def test_a_dictated_opening_said_without_punctuation(self):
        it = dictation.interpret("um so this is a sixty eight year old right handed female who comes in for dizziness that started about three "
                                 "weeks ago she says it's a spinning sensation when she rolls over in bed to the right", "hpi")
        hpi = self.hpi(it.append["ChiefComplaint-descr"], **it.set)
        self.assertTrue(hpi.startswith("Ms. **** is a 68 y/o RHD female presenting to the clinic today for a consultation on dizziness."), hpi)
        self.assertNotIn("this is a 68", hpi.lower())

    def test_unpunctuated_dictation_is_drafted_like_typed_notes(self):
        it = dictation.interpret("seventy one year old right handed male here for dizziness started about two months ago spinning when he lies "
                                 "down or rolls over in bed lasts less than a minute denies hearing loss and tinnitus has not had any falls takes "
                                 "lisinopril for blood pressure", "hpi")
        v, _out = build_until_stable(it.append["ChiefComplaint-descr"], **{k: val for k, val in it.set.items()})
        hpi = v["ChiefComplaint-descr"]
        self.assertIn("Onset ~2 months ago.", hpi)
        self.assertNotIn("spinning when he lies down", hpi)            # said by the drafted line
        self.assertIn("lisinopril", hpi)

    def test_dictation_in_a_symptom_section_isnt_said_twice(self):
        it = dictation.interpret("dizziness: patient reports spinning when she rolls to the left lasting about thirty seconds no nausea no "
                                 "hearing changes", "hpi")
        v, _out = build_until_stable(it.append.get("ChiefComplaint-descr", ""), **it.set)
        hpi = v["ChiefComplaint-descr"]
        self.assertEqual(hpi.count("Pt reports"), 1)
        self.assertEqual(hpi.count("seconds"), 1)

    def test_a_stopped_medication_is_not_one_to_add(self):
        out = compose.compose_note({"ChiefComplaint-descr": "F/u for BPPV. Much improved. Stopped meclizine.", "neuro-progress": True})
        self.assertFalse(any("Meclizine" in t and "not on the medication list" in t for t in out.todo))
        self.assertTrue(any("meclizine" in t.lower() and "review" in t.lower() for t in out.todo))


class ReviewedDictationTests(unittest.TestCase):
    """Dictation reviewers found ticking the opposite of what was said, or dropping it."""

    def said(self, text, section="hpi"):
        return dictation.interpret(text, section)

    def test_not_never_and_nt_are_denials(self):
        cases = [("patient states the spinning lasts a few hours, with nausea and vomiting, not with headache", "dizziness", "assoc-headache"),
                 ("he is not having any headaches anymore since starting the nortriptyline ten milligrams at night", "headache", "headache"),
                 ("tinnitus high pitched ringing in the right ear constant for about six months not pulsatile worse at night", "tinnitus", "t-pulsatile"),
                 ("tinnitus in both ears it's not constant comes and goes", "tinnitus", "t-course-chronic"),
                 ("dizziness lasts minutes not hours", "dizziness", "duration-hours"),
                 ("she doesn't have any hearing loss", "hearing", "hearingloss"),
                 ("she has never had true vertigo just lightheadedness", "dizziness", "assoc-vertigo"),
                 ("Dizziness, not spinning.", "hpi", "assoc-vertigo"), ("Tinnitus, left ear, not pulsatile.", "hpi", "t-pulsatile"),
                 ("Hearing loss, left, not fluctuating.", "hpi", "hlassoc-hearingfluc"),
                 ("Dizziness, worse with standing, never when lying down.", "hpi", "worse-supine"),
                 ("Gait: never uses a cane.", "hpi", "ga-better-cane"),
                 ("dizziness not worse with standing worse with turning the head lasts hours", "dizziness", "worse-standing")]
        for text, section, wrong in cases:
            with self.subTest(text=text):
                it = self.said(text, section)
                self.assertIsNot(it.set.get(wrong), True)
        self.assertTrue(self.said("she doesn't have any hearing loss", "hearing").set.get("hearingloss-denies"))
        self.assertTrue(self.said("tinnitus in both ears it's not constant comes and goes", "tinnitus").set.get("t-course-parox"))
        self.assertTrue(self.said("dizziness lasts minutes not hours", "dizziness").set.get("duration-minutes"))
        for text in ("Dizziness, not spinning.", "Dizziness, worse with standing, never when lying down.", "Gait: never uses a cane."):
            self.assertTrue(self.said(text).append, text)                # the words that no box says are kept

    def test_hedged_words_are_not_ticked(self):
        it = self.said("hearing: decreased hearing left ear for about a year gradual not sure if fluctuating, hearing aids on the left help")
        self.assertIsNot(it.set.get("hlassoc-hearingfluc"), True)
        self.assertTrue(it.set.get("hl-left"))
        it = self.said("so um the tinnitus is in both ears, it's a high pitched ringing, worse at night, maybe pulsatile, not sure", "tinnitus")
        self.assertIsNot(it.set.get("t-pulsatile"), True)
        self.assertTrue(any(n.startswith("Unsure") for n in it.notes))

    def test_a_denial_covers_its_own_words_only(self):
        it = self.said("gait: she is unsteady when walking, no falls, she denies weakness in the legs but has numbness in her feet", "gait")
        self.assertIsNot(it.set.get("ga-assoc-sensory-denies"), True)
        self.assertTrue(it.set.get("ga-assoc-weakness-denies") and it.set.get("ga-falls-no"))
        it = self.said("dizziness: no spinning, it's more of a lightheaded feeling when she stands up, lasting a few seconds")
        self.assertTrue(it.set.get("dizziness") and it.set.get("duration-seconds") and it.set.get("worse-standing"))
        it = self.said("hearing gradual decrease in the left ear over two years no fluctuation uses a hearing aid on the left", "hearing")
        self.assertIsNot(it.set.get("hearingloss-denies"), True)
        self.assertTrue(it.set.get("hl-left") and it.set.get("hlbetter-hearingaids"))
        it = self.said("um so this is a sixty eight year old right handed female who comes in for dizziness that started about three weeks ago "
                       "she says it's a spinning sensation when she rolls over in bed to the right lasts maybe thirty seconds no hearing loss no "
                       "ringing she did have a fall last week but no head injury")
        self.assertIsNot(it.set.get("ga-falls-no"), True)
        self.assertEqual((it.set.get("HPI-age"), it.set.get("hpi-female")), ("68", True))
        self.assertIn("She did have a fall last week but no head injury.", it.append["ChiefComplaint-descr"])
        self.assertTrue(self.said("no diabetes no stroke has high blood pressure and migraines as a teenager", "pmh").set.get("hist-hypt"))
        self.assertIsNot(self.said("no longer spinning now it's more of a constant imbalance", "gait").set.get("gaitataxia-denies"), True)
        it = self.said("no nausea but dizziness when she stands up", "dizziness")
        self.assertTrue(it.set.get("dizziness") and not it.set.get("dizziness-denies"))
        it = self.said("no hearing loss in the right ear, but hearing loss in the left ear for two years", "hearing")
        self.assertTrue(it.set.get("hl-left"))
        self.assertIsNot(it.set.get("hearingloss-denies"), True)
        it = self.said("denies tinnitus except for a high pitched ringing in the left ear at night", "tinnitus")
        self.assertFalse(it.set.get("tinnitus-denies") or it.set.get("tinnitus"))

    def test_otherwise_normal_doesnt_sign_off_what_was_said(self):
        it = self.said("exam otherwise normal positive head impulse to the right left beating nystagmus in primary gaze Romberg positive falls "
                       "to the right", "exam")
        for row in ("normal40", "normal24", "normal27"):
            self.assertIsNot(it.set.get(row), True, row)
        self.assertEqual(it.set.get("abnormal40"), "X")
        self.assertTrue(it.set.get("normal8"))
        it = self.said("exam normal except decreased vibration in both feet and absent ankle reflexes", "exam")
        self.assertIsNot(it.set.get("normal33"), True)
        self.assertIsNot(it.set.get("normal36"), True)
        self.assertEqual(it.set.get("findings33"), "Decreased vibration in both feet")
        self.assertTrue(it.set.get("normal8"))

    def test_numbered_impressions(self):
        it = self.said("impression one vestibular migraine with two to three attacks a month dash start magnesium two right ear hearing loss "
                       "dash audiogram", "impression")
        self.assertEqual(it.append["impression-plan-descr"], "1) Vestibular migraine with 2 to 3 attacks a month - Start magnesium\n"
                                                             "2) Right ear hearing loss - Audiogram")
        it = self.said("impression one migraine two vestibular migraine dash start magnesium four hundred milligrams nightly three hearing loss",
                       "impression")
        self.assertEqual(it.append["impression-plan-descr"], "1) Migraine\n2) Vestibular migraine - Start magnesium 400 mg nightly\n3) Hearing loss")
        it = self.said("impression one hypertension dash continue lisinopril ten milligrams per her pcp two dizziness", "impression")
        self.assertIn("1) Hypertension - Continue lisinopril 10 mg per her PCP\n2) Dizziness", it.append["impression-plan-descr"])
        it = self.said("Impression one BPPV right posterior canal. Plan Epley today, follow up in four weeks.", "impression")
        self.assertEqual(it.append["impression-plan-descr"], "1) BPPV right posterior canal - Plan Epley today")
        self.assertTrue(it.set.get("imp-rightpost"))

    def test_relief_and_aggravation_need_their_cue(self):
        self.assertIsNot(self.said("stop meclizine and start vestibular therapy", "plan").set.get("plan-meds"), True)
        self.assertIn("plan-descr", self.said("stop meclizine and start vestibular therapy", "plan").append)
        self.assertIsNot(self.said("plan start vestibular rehab avoid meclizine except for severe attacks", "plan").set.get("plan-meds"), True)
        it = self.said("headaches have been worse since she started the new medication")
        self.assertIsNot(it.set.get("h-better-meds"), True)
        self.assertEqual(it.section, "hpi")
        self.assertIsNot(self.said("walks with a walker no falls since last visit", "gait").set.get("ga-worse-walk"), True)

    def test_words_no_box_takes_are_kept(self):
        for text, section in (("dizziness worse with standing better with sitting down no nausea or vomiting", "dizziness"),
                              ("ringing in the left ear, worse at night", "tinnitus"), ("dizzy when standing, only in the mornings", "dizziness")):
            it = self.said(text, section)
            self.assertTrue(it.append, text)
        it = self.said("dizziness started three weeks ago after a cold and she has been nauseous since")
        self.assertEqual(it.section, "hpi")
        self.assertNotIn("***", it.append["ChiefComplaint-descr"])
        it = self.said("Dizziness is constant for the past two weeks, more like rocking.")
        self.assertTrue(it.append["ChiefComplaint-descr"].startswith("Dizziness is constant"))
        it = self.said("labs from her primary care last month were normal")
        self.assertEqual((it.section, it.append["ChiefComplaint-descr"]), ("hpi", "Labs from her primary care last month were normal."))
        it = self.said("um she's a seventy eight year old female with imbalance for two years, uses a walker, fell twice last month")
        self.assertEqual((it.set.get("HPI-age"), it.set.get("hpi-female")), ("78", True))

    def test_positional_testing_sides(self):
        it = self.said("left dix hallpike positive with torsional nystagmus fatigable with latency right side normal", "goggles")
        self.assertTrue(it.set.get("dixhall-left-torsion") and it.set.get("dixhall-right-normal"))
        self.assertIsNot(it.set.get("dixhall-right-torsion"), True)
        self.assertNotIn("goggle-treatment-x", it.append)
        it = self.said("dix hallpike positive on the right with upbeating torsional nystagmus", "goggles")
        self.assertTrue(it.set.get("dixhall-right-torsion"))
        self.assertIsNot(it.set.get("supine-center-upbeat"), True)
        self.assertTrue(self.said("geotropic nystagmus to the left more than right", "goggles").set.get("supine-left-geo"))
        self.assertIsNot(self.said("geotropic nystagmus to the left more than right", "goggles").set.get("supine-right-geo"), True)

    def test_someone_elses_numbers(self):
        self.assertNotIn("HPI-age", self.said("dizziness began after she was rear ended by a 19 year old male driver in august").set)
        it = self.said("she has had ear pain 3 days and lost weight 15 pounds since march")
        self.assertFalse({"WEIGHT", "const-pain"} & set(it.set))
        self.assertNotIn("next-appt", self.said("she saw cardiology who will follow up in two weeks").set)

    def test_falls_said_in_the_gait_section(self):
        self.assertTrue(self.said("she has fallen twice in the last month", "gait").set.get("ga-falls-yes"))
        self.assertTrue(self.said("two falls in the last six months", "gait").set.get("ga-falls-yes"))
        self.assertIsNot(self.said("yes she uses a cane", "gait").set.get("ga-falls-yes"), True)
        self.assertIsNot(self.said("no falls", "gait").set.get("gaitataxia"), True)

    def test_change_is_not_a_denial(self):
        self.assertIsNot(self.said("no new hearing loss since last visit").set.get("hearingloss-denies"), True)
        self.assertIsNot(self.said("no improvement in the dizziness since last visit", "dizziness").set.get("dizziness-denies"), True)

    def test_spoken_numbers(self):
        it = self.said("vitals blood pressure one forty two over eighty eight heart rate seventy eight", "vitals")
        self.assertEqual((it.set["const-bp"], it.set["const-hr"]), ("142/88", "78"))
        it = self.said("total time thirty minutes follow up in two to three months", "plan")
        self.assertEqual((it.set["appt-time"], it.set["next-appt"]), ("30", "2-3"))
        self.assertEqual(self.said("total time forty five minutes", "plan").set["appt-time"], "45")
        it = self.said("medications meclizine twenty five milligrams as needed lisinopril ten milligrams daily metformin five hundred twice a day", "meds")
        self.assertEqual(it.append["meds-descr"], "Meclizine 25 mg prn.\nLisinopril 10 mg daily.\nMetformin 500 2x/day.")

    def test_spoken_punctuation(self):
        it = self.said("she has dizziness period no hearing loss period new paragraph she takes meclizine comma which helps")
        self.assertEqual(it.append["ChiefComplaint-descr"], "She has dizziness. No hearing loss.\nShe takes meclizine, which helps.")

    def test_sides_as_said(self):
        it = self.said("hearing loss on the right, not the left", "hearing")
        self.assertTrue(it.set.get("hl-right"))
        self.assertIsNot(it.set.get("hl-left"), True)
        self.assertTrue(self.said("ringing in the right ear, not the left", "tinnitus").set.get("t-right"))
        self.assertTrue(self.said("hearing loss, right worse than left", "hearing").set.get("hl-bilat"))
        for text in ("she left work early because of the ringing", "ringing started right after the concert"):
            it = self.said(text, "tinnitus")
            self.assertFalse(it.set.get("t-left") or it.set.get("t-right"), text)
        it = self.said("hearing aids in both ears, no tinnitus", "hearing")
        self.assertFalse(it.set.get("hearingloss") or it.set.get("hl-bilat"))

    def test_what_is_over(self):
        it = self.said("used to have ringing in the left ear, that went away years ago", "tinnitus")
        self.assertFalse(it.set.get("tinnitus") or it.set.get("t-left"))

    def test_two_negatives(self):
        self.assertIsNot(self.said("not without some imbalance in the dark", "gait").set.get("gaitataxia-denies"), True)

    def test_falls_said_with_the_vitals_are_kept(self):
        it = self.said("blood pressure 128 over 76, pulse 72, no falls since last visit", "vitals")
        self.assertEqual(it.append.get("ChiefComplaint-descr"), "No falls since last visit.")


class DictatedSectionTests(unittest.TestCase):
    """Narrative dictated under a symptom's section goes to the HPI; the section's name goes in front only when the
    dictation said the patient has the symptom."""

    def _story(self, text, section):
        from recordfinder.charting import dictation
        it = dictation.interpret(text, section).as_dict()
        v = {"ChiefComplaint-descr": it["append"].get("ChiefComplaint-descr", ""), **it["set"]}
        out = compose.compose_note(v)
        return it, [i["text"] for p in out.proposals if p.id == "story" for i in p.items]

    def test_falls_and_aids_are_not_the_symptom(self):
        for text, section in (("she has fallen twice in the last month", "gait"), ("walks with a walker no falls since last visit", "gait"),
                              ("hearing aids in both ears, no tinnitus", "hearing")):
            it, items = self._story(text, section)
            self.assertNotIn("Gait ataxia: present", items)
            self.assertNotIn("Hearing loss: present", items)
            self.assertFalse(it["append"].get("ChiefComplaint-descr", "").startswith(("Gait ", "Hearing loss ")))

    def test_a_symptom_said_present_keeps_its_subject(self):
        it, _ = self._story("worse when she stands up and lasts a few seconds each time", "dizziness")
        self.assertTrue(it["append"]["ChiefComplaint-descr"].startswith("Dizziness "))
