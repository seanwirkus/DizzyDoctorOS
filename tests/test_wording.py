"""Rough notes and dictation in the clinic's wording: fixed rules, meaning unchanged, and running it twice changes
nothing. Each case is (what was typed or said, what the note should say)."""
import re
import unittest
from unittest import mock

from recordfinder.charting import wording
from tests.test_story_meds import DIZZY, HEADACHE, MEMORY, NUMBNESS, TREMOR

CASES = [
    # tidying: spacing, capitals, one sentence ending in a period
    ("", ""),
    ("  dizziness   worse   standing  ", "Dizziness worse standing."),
    ("dizzy. worse standing", "Dizzy. Worse standing."),
    ("No falls", "No falls."),
    ("Any falls?", "Any falls?"),
    ("dizzy , worse standing ,, better sitting", "Dizzy, worse standing, better sitting."),
    ("couldnt stand, wasnt sure", "Couldn't stand, was uncertain."),
    ("line one\n\n\nline two", "Line one.\n\nLine two."),
    # a thought that stops is marked for the provider to finish
    ("Symptoms last seconds to...", "Sxs last seconds to ***"),
    ("Symptoms seem worse when", "Sxs seem worse when ***"),
    ("Plan to follow up in", "Plan to follow up in ***"),
    ("no headaches usually, well occasionally… PCP exam normal per pt", "No headaches usually, well occasionally *** PCP exam normal per pt."),
    ("4 cups coffee a day... asking if he should cut salt", "4 cups coffee a day *** Asking if he should cut salt."),
    # filler goes; "like", "just" and "really" only where they are plainly filler
    ("um, she has been dizzy", "She has been dizzy."),
    ("so, uh, the room spins", "The room spins."),
    ("she, like, feels off balance", "She feels off balance."),
    ("it's basically worse in the morning", "It's worse in the morning."),
    ("you know, the dizziness is worse at night", "The dizziness is worse at night."),
    ("it just feels like the room is moving", "It feels like the room is moving."),
    ("dizzy for two months, um, maybe hearing loss", "Dizzy for 2 months, possible hearing loss."),
    ("Headaches? Not really.", "Headaches? Not really."),
    ("it's just at night", "It's just at night."),
    ("pretty much all day", "Nearly all day."),
    ("it's pretty much gone", "It's mostly gone."),
    ("like 3 times a week", "~3x/week."),
    # reported speech
    ("Patient says she fell once.", "Pt reports she fell once."),
    ("The patient states the room spins.", "Pt reports the room spins."),
    ("States the room spins.", "Reports the room spins."),
    ("Mentions ringing in the left ear.", "Reports ringing AS."),
    ("Also mentions legs feel unsteady.", "Also reports legs feel unsteady."),
    ("One fall but says tripped over rug.", "One fall but reports tripped over rug."),
    ("daughter says thats old not new", "Daughter reports that's old not new."),
    ("Patient said that she fell.", "Pt reported she fell."),
    ("She says the room spins.", "She reports the room spins."),
    ("complains of dizziness", "C/o dizziness."),
    ("per patient report", "Per pt report."),
    # hedges kept, in the clinic's words
    ("maybe hearing loss", "Possible hearing loss."),
    ("started maybe January", "Started possibly January."),
    ("maybe 6 or 8 months", "Possibly 6 or 8 months."),
    ("Father had tremor maybe Parkinson's.", "Father had tremor possible Parkinson's."),
    ("Mood maybe depressed", "Mood possibly depressed."),
    ("veers to the left maybe", "Veers to the left possibly."),
    ("not sure if same thing", "Uncertain if same thing."),
    ("unsure which one", "Uncertain which one."),
    ("he wasn't sure what the pills were", "He was uncertain what the pills were."),
    ("perhaps a few seconds", "Possibly a few seconds."),
    ("possible BPPV, probably right side", "Possible BPPV, probably right side."),
    # the clinic's shorthand
    ("dizziness with nausea, without vomiting", "Dizziness w/ nausea, w/o vomiting."),
    ("history of migraines", "Hx of migraines."),
    ("Has history of HTN", "Has hx of HTN."),
    ("past medical history significant for DM", "PMHx significant for DM."),
    ("Symptoms worse in the morning, symptom free at night", "Sxs worse in the morning, sx free at night."),
    ("blood pressure medication changed", "BP medication changed."),
    ("no loss of consciousness", "No LOC."),
    ("ringing in the left ear", "Ringing AS."),
    ("hearing loss in both ears", "Hearing loss AU."),
    ("fullness in R ear", "Fullness AD."),
    ("right ear fullness", "Right ear fullness."),
    ("67-year-old right-handed female", "67 y/o RHD female."),
    ("73 year old left handed man", "73 y/o LHD man."),
    ("51yoF woke up dizzy", "51 y/o F woke up dizzy."),
    ("52 y.o. F c/o tinnitus", "52 y/o F c/o tinnitus."),
    ("seventy-three years old", "73 y/o."),
    ("about 3 weeks ago", "~3 weeks ago."),
    ("for about a year", "For ~1 year."),
    ("around two months", "~2 months."),
    ("approximately 10 minutes", "~10 minutes."),
    ("worried about 2 things", "Worried about 2 things."),
    ("three times a week", "3x/week."),
    ("twice a day", "2x/day."),
    ("about 2x a month", "~2x/month."),
    ("3-4 times per week", "3-4x/week."),
    ("two or three times daily", "2-3x/day."),
    ("two weeks ago", "2 weeks ago."),
    ("One day she fell", "One day she fell."),
    ("for one day", "For 1 day."),
    ("ten, fifteen seconds", "10, 15 seconds."),
    # negations and the clinic's own sentences come through untouched
    ("denies hearing loss, denies tinnitus", "Denies hearing loss, denies tinnitus."),
    ("doesnt smoke, wine on weekends", "Doesn't smoke, wine on weekends."),
    ("never smoked, no alcohol", "Never smoked, no alcohol."),
    ("No weakness, although left arm felt heavy", "No weakness, although left arm felt heavy."),
    ("Pt reports paroxysmal vertigo lasting seconds, worse w/ standing.", "Pt reports paroxysmal vertigo lasting seconds, worse w/ standing."),
    ("Denies associated fluctuating hearing loss, tinnitus or aural fullness.", "Denies associated fluctuating hearing loss, tinnitus or aural fullness."),
    ("No other focal motor, sensory, or CN deficits reported at this time.", "No other focal motor, sensory, or CN deficits reported at this time."),
    ("Assoc w/ tinnitus AS. Onset 3 weeks ago.", "Assoc w/ tinnitus AS. Onset 3 weeks ago."),
    ("Meclizine 12.5 mg prn, used once in interim.", "Meclizine 12.5 mg prn, used once in interim."),
    ("Dr. Smith saw her. vs. last time", "Dr. Smith saw her. Vs. last time."),
]

# What reviewers found the rules getting wrong: (what was typed or said, what the note should say). Each keeps every
# negation, hedge, number, unit, side and drug name of the original.
REVIEW_CASES = [
    # "about" / "like" belong to the verb before them; they are "~" only before an amount
    ("Concerned about 3 falls this year.", "Concerned about 3 falls this year."),
    ("She would like 2 weeks to think about surgery.", "She would like 2 weeks to think about surgery."),
    ("It felt like 10 minutes.", "It felt like 10 minutes."),
    ("Asks about 4 days of PT.", "Asks about 4 days of PT."),
    ("She is worried about 2 falls last month.", "She is worried about 2 falls last month."),
    ("Head feels like 10 pounds of pressure.", "Head feels like 10 pounds of pressure."),
    ("It seems like 2 weeks ago it started.", "It seems like 2 weeks ago it started."),
    # how often, the same on the first pass as on the second
    ("Takes meclizine like twice a day.", "Takes meclizine ~2x/day."),
    ("HA about once a week, maybe less.", "HA ~1x/week, possibly less."),
    ("She's still getting the headaches about twice a week though.", "She's still getting the headaches ~2x/week though."),
    ("spinning lasts about a minute or two", "Spinning lasts ~1-2 minutes."),
    # doses, decimals, years and blood pressures said in words are written in digits
    ("She takes meclizine twelve point five milligrams as needed, maybe twice a week. Started sertraline fifty milligrams about a month ago.",
     "She takes meclizine 12.5 mg prn, possibly 2x/week. Started sertraline 50 mg ~1 month ago."),
    ("the dizziness is maybe fifty percent better", "The dizziness is possibly 50% better."),
    ("gabapentin three hundred milligrams three times a day", "Gabapentin 300 mg 3x/day."),
    ("March of twenty twenty three", "March of 2023."),
    ("A1c was seven point two", "A1c was 7.2."),
    ("BP was one twenty eight over seventy six", "BP was 128/76."),
    # an ear is AD / AS only "in" or "of" it, and both ears only where they are the place of the symptom
    ("Pain in the right ear canal.", "Pain in the right ear canal."),
    ("Covers both ears when it's loud.", "Covers both ears when it's loud."),
    ("Ringing in her R ear and L ear", "Ringing AU."),
    ("Sharp pain behind the left ear with the headaches.", "Sharp pain behind the left ear w/ the headaches."),
    ("Headache radiates to the right ear.", "Headache radiates to the right ear."),
    ("cant hear out of the left ear at all", "Can't hear out of the left ear at all."),
    # "maybe" after a verb, or before a clause, is "possibly"; a decision isn't a report
    ("Started maybe Monday.", "Started possibly Monday."),
    ("Maybe hearing aids help.", "Possibly hearing aids help."),
    ("started maybe Tuesday after the flight", "Started possibly Tuesday after the flight."),
    ("took maybe Meclizine or Dramamine", "Took possibly Meclizine or Dramamine."),
    ("He said no to VRT.", "He said no to VRT."),
    ("PCP told me to stop the meclizine.", "PCP told me to stop the meclizine."),
    ("ENT said to use Flonase twice a day.", "ENT said to use Flonase 2x/day."),
    # months and eponyms with their capitals
    ("maybe 4 or 5 attacks since march.", "Possibly 4 or 5 attacks since March."),
    ("BP meds were increased in august", "BP meds were increased in August."),
    ("maybe meniere's? maybe migraine", "Possible Meniere's? Possible migraine."),
    # a correction keeps its "actually"; "maybe more" is a whole thought
    ("no actually it started after the cruise and it never went away", "No actually it started after the cruise and it never went away."),
    ("vertigo maybe twice a week, maybe more", "Vertigo possibly 2x/week, possibly more."),
    ("she has been nauseous since", "She has been nauseous since."),
    # an age is a person's; softeners stay softeners
    ("Her hearing aids are 5 years old and the house she lives in is 100 years old.",
     "Her hearing aids are 5 years old and the house she lives in is 100 years old."),
    ("pretty much never dizzy anymore", "Almost never dizzy anymore."),
    ("pretty much no ringing now", "Almost no ringing now."),
    ("basically normal hearing per audiogram", "Essentially normal hearing per audiogram."),
    ("She is not sure-footed on stairs.", "She is not sure-footed on stairs."),
    # a "?" in front of a word is a hedge on that word, not the end of the sentence
    ("Hearing loss AD, ? AS too.", "Hearing loss AD, ? AS too."),
    ("Referred for ? BPPV by PCP.", "Referred for ? BPPV by PCP."),
    # dictation filler
    ("um she says the dizziness is like um kind of a rocking feeling you know like being on a boat",
     "She reports the dizziness is kind of a rocking feeling like being on a boat."),
    ("So she's back for follow up.", "She's back for follow up."),
    ("He's had, um, like three falls in the last six months. No head injury.", "He's had ~3 falls in the last 6 months. No head injury."),
    ("so the patient is a 71 year old right handed male who presents with um dizziness", "Pt is a 71 y/o RHD male who presents w/ dizziness."),
]

# The five notes a provider types between rooms, in full.
NOTES = [
    (TREMOR, "67 y/o w/ shaking in both hands possibly 6 or 8 months, worse w/ holding cup and writing, sometimes reports happens at "
             "rest but uncertain. Father had tremor possible Parkinson's. Also feels slower lately and handwriting smaller but then "
             "reports only when tired. No falls except one in yard. Caffeine 3-4 cups. Sxs seem worse when ***"),
    (HEADACHE, "42 y/o female w/ headaches started ~3 weeks ago or possibly longer, pressure behind right eye then sometimes whole "
               "head. Reports blurry vision off and on, no clear double vision. Nausea once. Took ibuprofen helped a little. Has hx of "
               "migraines but reports this feels different because more constant. No weakness, although left arm felt heavy "
               "yesterday for about ***"),
    (NUMBNESS, "58 y/o presents for numbness right hand and sometimes foot. Started after waking up one morning ~2 months ago, comes "
               "and goes. Mostly first 3 fingers but sometimes whole hand. Dropping objects occasionally. Neck pain for years. Denies "
               "weakness but later reports grip feels weaker. No bowel or bladder changes. Had MRI somewhere possibly 2024 but does "
               "not remember results and ***"),
    (DIZZY, "73 y/o w/ dizziness described as lightheaded but also room moving sometimes. Worse standing up and when turning head "
            "quickly. Started possibly January, more frequent this month. One fall but reports tripped over rug. No LOC. Has ringing "
            "AS, possible hearing loss. Also reports legs feel unsteady especially at night. BP medication changed recently but "
            "uncertain which one. Sxs last seconds to ***"),
    (MEMORY, "71 y/o brought for memory concerns, family noticed repeating questions and forgetting appointments for ~1 year. Pt "
             "reports memory is fine and mostly stress. Still driving, got lost once but reports road was closed. Misplaces phone and "
             "keys. Sleep poor, wakes several times. Mood possibly depressed since spouse died. No hallucinations. Handles medications "
             "independently although daughter reports sometimes doses are missed. Unclear when sxs first really ***"),
]


class ClinicalWordingTests(unittest.TestCase):
    def test_each_rule(self):
        for said, written in CASES:
            with self.subTest(said=said):
                self.assertEqual(wording.clinical(said), written)

    def test_what_reviewers_found(self):
        for said, written in REVIEW_CASES:
            with self.subTest(said=said):
                self.assertEqual(wording.clinical(said), written)
                self.assertEqual(wording.clinical(written), written)          # and a second pass changes nothing
                self.assertTrue(wording.keeps_meaning(said, written) or said == written)

    def test_spoken_numbers(self):
        self.assertEqual(wording.spoken_numbers("blood pressure one forty two over eighty eight", everywhere=True), "blood pressure 142/88")
        self.assertEqual(wording.spoken_numbers("total time forty five minutes", everywhere=True), "total time 45 minutes")
        self.assertEqual(wording.spoken_numbers("in nineteen eighty five"), "in 1985")
        self.assertEqual(wording.spoken_numbers("two weeks"), "two weeks")                   # left to the count rule
        self.assertEqual(wording.dose_words("start magnesium 400 milligrams nightly"), "start magnesium 400 mg nightly")

    def test_the_five_rough_notes(self):
        for typed, written in NOTES:
            with self.subTest(typed=typed[:40]):
                self.assertEqual(wording.clinical(typed), written)

    def test_running_it_twice_changes_nothing(self):
        for text in [c[0] for c in CASES + REVIEW_CASES] + [c[1] for c in CASES] + [n[0] for n in NOTES]:
            with self.subTest(text=text[:40]):
                once = wording.clinical(text)
                self.assertEqual(wording.clinical(once), once)

    def test_every_negation_and_hedge_comes_through(self):
        for text in [c[0] for c in CASES] + [n[0] for n in NOTES]:
            with self.subTest(text=text[:40]):
                self.assertTrue(wording.keeps_meaning(text, wording.clinical(text)))

    def test_the_check_catches_a_changed_meaning(self):
        self.assertFalse(wording.keeps_meaning("no dizziness", "dizziness"))
        self.assertFalse(wording.keeps_meaning("maybe hearing loss", "hearing loss"))
        self.assertFalse(wording.keeps_meaning("not sure it helps", "it helps"))
        self.assertFalse(wording.keeps_meaning("3 weeks", "2 weeks"))
        self.assertFalse(wording.keeps_meaning("3 weeks", "3 months"))
        self.assertFalse(wording.keeps_meaning("ringing in left ear", "ringing AD"))
        self.assertFalse(wording.keeps_meaning("meclizine 25 mg", "25 mg"))
        self.assertTrue(wording.keeps_meaning("ringing in left ear", "ringing AS"))
        self.assertTrue(wording.keeps_meaning("not sure which one", "uncertain which one"))
        self.assertTrue(wording.keeps_meaning("twice a day", "2x/day"))

    def test_a_rewrite_that_would_change_the_meaning_is_not_used(self):
        # a rule that lost a word that matters: the sentence is kept in the provider's words, only tidied
        broken = wording.WORD_RULES + [(re.compile(r"\bno\s+", re.I), "")]
        with mock.patch.object(wording, "WORD_RULES", broken):
            self.assertEqual(wording.clinical("no dizziness with standing"), "No dizziness with standing.")
        self.assertEqual(wording.clinical("no dizziness with standing"), "No dizziness w/ standing.")
        self.assertEqual(wording.clinical("Maybe. Maybe not."), "Possibly. Possibly not.")

    def test_numbers_in_words(self):
        self.assertEqual(wording.word_number("seventy-three"), 73)
        self.assertEqual(wording.word_number("twelve"), 12)
        self.assertIsNone(wording.word_number("several"))


if __name__ == "__main__":
    unittest.main()
