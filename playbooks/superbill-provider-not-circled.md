# Superbill: provider or attending not circled

**The request:** billing (MPS) posts in the Teams billing chat, for example
`ACCOUNT 123456 PT First M Last DOS 09/24/2026 EC. PROVIDER NOT CIRCLED`.
The letters after the DOS are the batch code, which is the provider who saw the patient.

**In DizzyDoctor classic tools:** paste the chat, select the request, click **Superbill…**, check the page and the suggestion, then click **Amend & file**. The rest of this page is what the app does, so a person can do it by hand or check the app's work.

## 1. Find the scanned batch

- **Where:** `V:\1111111Kyla MPS Superbills All Providers\`. The current month's folder is at the top level (`September 2026\`); older months are under the year (`2026\August 2026\`). Once a batch has gone to billing it moves into the month's `Uploaded\` folder.
- **File name:** `<CODE> <mm.dd.yy>.pdf`, for example `EC 09.24.26.pdf`. Staff sometimes add a word after the date, as in `EC 09.14.26 Amended.pdf`.

| Code | Provider | Outlook calendar |
|---|---|---|
| IMP | Ian Purcell, MD, PhD | IMP |
| MP, MP 2 | Monali Patel, MD | Dr. Patel |
| TY, TY EMG NCV | Touraj Yari, PA-C | Touraj / TY EMG/NCV |
| JP | Jake Perkins, PA-C | Jake Perkins |
| CC | Carter Copeland, PA-C | Carter Copeland |
| EC | Evan Camarillo, PA-C | Evan Camarillo |
| EEG, VNG, TYMP | testing | EEG / VNG_VEMP |

## 2. Find the patient's page in the batch

- The first page or two are that provider's printed schedule for the day.
- Then comes one **packet** per patient: the superbill, followed by that patient's own pages (insurance cards, ID, referrals and authorizations, intake forms, return questionnaire), up to the next superbill.
- The name on the superbill is handwritten. Identify the patient from the **printed** pages in the packet.
- Packets follow the schedule order. When two visits start at the same time, the longer one (usually the new patient) comes first.

## 3. Check the Outlook schedule

Classic Outlook, staff mailbox. Open the provider's calendar on the DOS. The appointment subject has the time, `Ins:` (payer), `Sec:`, copay and the referral; the tags hold things like `HMO / AUTH`, `Follow-Up`, `New Patient` and `IMP has to be present`.

## 4. Decide the attending physician (for PA visits)

Work down this list and stop at the first rule that applies:

1. **The appointment is tagged `IMP has to be present` or `MP TO BE PRESENT`:** use that physician.
2. **Payer rules.** Dr. Patel's calendar says *"DO NOT SCHEDULE SCMG PATIENTS WITH DR. PATEL"*, so SCMG patients are never hers, which leaves Dr. Purcell.
3. **The referral or authorization in the packet** names the physician it was issued to, often *Ian Purcell, MD / IAN PURCELL MD INC*.
4. **Anyone out that day:** look for a note that names the physician with OUT/OFF/vacation. Notes like `WM OUT 12 LATEST`, `NR in @ 11` or `mc out` are staff initials, not physicians.
5. **Clinic days.** Dr. Patel's clinic days carry her "First Patient at 9:00 AM…" template (Mondays and Wednesdays in Sept 2026). Dr. Purcell's calendar has a standing **DNS (do not schedule)** block from 8 to 4 almost every day. That block is a booking rule, **not** an absence.
6. **Otherwise** default to Dr. Purcell, and confirm.

**Medicare patients:** the superbill's "MEDICARE INCIDENT-TO MET" row needs YES or NO; decide it per visit. Leave it blank for everyone else.

## 5. Mark the superbill

Mark it the way the other superbills in the batch are marked:

- Circle the PA in the **PROVIDER** list.
- Put a circled X in the attending's column on the PA's row: the **IMP** column (left) for Dr. Purcell, or the **MP** column (right) for Dr. Patel.

Do this in **red on a copy**, with a stamp: `AMENDED <date time> *<initials>`, the provider, the attending, the evidence (for example "Outlook sched 9/24 10:00 (Evan Camarillo)"), the account and DOS, and "Processed via DizzyDoctor". Never edit the original batch.

## 6. File it

| Where | Name |
|---|---|
| V: drive, in the same `Uploaded\` folder as the batch | `<CODE> <mm.dd.yy> <Last> <First> Amended.pdf` |
| ShareFile (`mpspros.sharefile.com`) › **PURCELL › <YYYY> › <Month> › Missing** | `<Last> <First> <CODE> <mm.dd.yy> SB.pdf` |

For reference, the ShareFile `PURCELL/<YYYY>/<Month>/` folder holds the day batches sent to billing. It has three subfolders:

- **Missing:** documents billing asked for, including amended superbills.
- **Copay:** `COPAY mm.dd.yy MR.pdf` logs.
- **Home Health**

## 7. Close the loop

- Put a **👍** on the billing message in Teams. That's the office's "done" signal, so no written reply is needed.
- The request is marked Done in DizzyDoctor, and the amendment is logged in the app.
