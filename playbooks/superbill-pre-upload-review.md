# Superbill batch review before upload

Every batch in the month folder gets this review **before** it goes to ShareFile. Afterwards, the batch (plus any `Amended` copy) moves into the month's `Uploaded` folder.

Each check compares three sources: the **superbill**, the patient's **Outlook bookings** on every calendar, and the **progress note** for that date (`V:\1111 Progress Notes\<year>\LastFirst mm dd yy.pdf`, a fillable PDF whose fields can be read directly). DizzyDoctor does the lookups; a person makes the calls.

## 1. Does every visit have its sheet?

- Count the provider's seen patients that day in Outlook against the superbills in the batch.
- A patient booked but without a sheet is usually an untagged no-show, a rescheduled visit, or a **phone visit** (tagged "Telephone Consultation"; those don't get a paper sheet). Confirm with the note: no note means no visit.
- Tests booked on the testing calendars (VNG_VEMP, TY EMG/NCV, EEG) belong to the patient only if the **name and DOB** match. A similar name at the same hour can be a different person.

## 2. The header

- DOB and DOS match Outlook and the note. Watch for a blank day ("09/ /26"), a digit misread, a 2-digit year typo.
- Fix in red above the box, with the source named in the stamp.

## 3. Visit level and add-ons

- New patient (first visit in 3 years of Outlook, note says consult/eval) at 60 min → 99205; follow-up at 40 min → 99215. The note's total time must support the level.
- **G2211** needs the note's longitudinal-care statement ("This visit was provided within the context of ongoing longitudinal care…"). A review-time or procedure-time line in that field does not count. Medicare doesn't pay G2211 when the E/M carries modifier 25 (same-day procedure).
- **Test modifier-25 lines** (TYMP, VNG, CDP, EMG/NCV, EEG) must match a test that's in the note or on a testing calendar that day. One oval often spans two lines: bill only what was done.

## 4. Procedures

- Goggle studies (92285 / 92541 / 92542 / 92546) when the note says IR oculography was done. If the note says it but nothing's circled, it's a possible missed charge.
- **95992 (canalith repositioning)** only when the note documents treatment that day, and only with a sided BPPV code: H81.11 right, H81.12 left, H81.13 bilateral (the form's own rule).
- Trigger points 20553: never with cervicalgia M54.2 (form rule).

## 5. Diagnoses

- **Uncertain diagnoses aren't coded in the office setting.** "Probable", "vs", "suspected", "rule out", "likely" in the note means code the symptoms (R42, R27.8, R26.89…), not the diagnosis. A negative IR oculography plus "probable BPPV" means no H81.1x.
- **Use the most specific code the note supports**: side documented → H81.11/H81.12 rather than H81.10; both sides → the bilateral code (G56.03 for carpal tunnel on both sides, not G56.01 + G56.02).
- **Manifestation codes need the cause coded first**, e.g. I68.0 (cerebral amyloid angiopathy) needs E85.4 before it.
- MCI due to Alzheimer's = G30.9 + G31.84 (no dementia code unless the note says dementia).
- A symptom that's part of a confirmed diagnosis is redundant (R51.9 with a confirmed migraine); an unspecified code next to specific ones from the same category (R20.9 with R20.1/R20.2) is usually dropped. Low priority.
- Every problem the note says was addressed should have a code. A sheet with only one of five problems coded goes back to the provider.

## 6. Provider box

- The rendering provider circled, and the attending physician marked in their column on that row.
- The **note's attestation** ("I, <physician>, was present…") must name the same physician as the superbill. The physician's own calendar that day is the tie-breaker (no patients booked → they weren't the attending).
- SCMG patients are never Dr. Patel's. "IMP has to be present" / "MP TO BE PRESENT" tags decide the attending.
- **Medicare (traditional) follow-ups**: YES or NO must be circled on "Medicare incident-to met". A Medicare **new** patient can't be incident-to.

## 7. HMO / authorization

- For HMO / AUTH visits, compare what was done with the authorization pages in the packet (service codes and dates). Anything done but not authorized goes to the referral coordinator before upload.

## Fixing and filing

1. Clear fixes (missing YES/NO, dates, bilateral codes, code-first additions) are made in **red on a copy**, one stamp per page: `AMENDED <date time> *<initials>`, what changed, and the evidence. The original scan is never edited.
2. Build `<CODE> mm.dd.yy Amended.pdf` (the original with the fixed pages swapped in) next to the original, and check that every other page is unchanged.
3. Upload the amended batch (or the original, if nothing changed) to ShareFile › PURCELL › `<year>` › `<Month>`.
4. Move the original and the amended copy into `<Month YYYY>\Uploaded`.
5. Decisions only a provider or billing can make (attestations, G2211 statements, missed charges, uncertain diagnoses) go to them as a list; they're not changed on the sheet.
