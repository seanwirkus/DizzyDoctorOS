# DizzyDoctor

The clinic's progress notes, schedule and patient charts in one window, laid out like Windows File Explorer, on
the clinic's Windows PCs and on a Mac.

- **Tabs across the top:** **Home** and one tab per open patient (**+** for a new note without a patient yet). Each
  patient tab keeps its own note exactly as you left it; an orange dot means unsaved changes, and notes left open
  with unsaved changes come back as tabs after a restart. Ctrl+Tab moves between tabs.
- **Home:** Calendar (day, week and month across every provider), Patients (the reconciled directory), Saved notes,
  and—only for administrators—Users & groups.
- **A patient tab:** the patient at the top (name in bold, as the note prints it) and the whole note in one scroll,
  laid out like the PDF's four pages (Page 1 Patient, Story, Medications, Dizziness, Hearing loss; Page 2 Tinnitus,
  Gait, Headache, History; Page 3 Exam, Oculography; Page 4 Studies, Plan). The left side lists those parts by page:
  click one to jump there; the part you're in stays lit as you scroll. Below them: Check, PDF and the patient's
  **Chart** (SnapShot, Chart review, Results / testing). A symptom's details open when it's marked present. Findings
  are coloured as in Epic: red for a positive or abnormal finding, green for a negative or normal one, grey until
  chosen. The visit timer is off unless Settings → Charting turns it on.
- **Dictate** (F9) for the provider talking to the note, or **Record visit** for the whole visit, provider and patient
  (after the patient agrees). Both are transcribed on this computer; when they stop, the boxes they make plain are
  ticked and **Build** opens with what was heard on top and the note written out in the clinic's wording, a dictated
  opening turned into the clinic's "Ms. X is a 64 y/o RHD female presenting..." line.
  The note is drafted by rules in the clinic's own wording, never by a language model, and dictation lands in the
  same wording rather than word for word.
- **Scope note import:** use one of the versioned templates in `docs/scope`, copy the generated note from Scope, then
  choose **Scope note** under **Build → Type or paste**. The app maps the fixed headings into the progress-note fields,
  leaves ambiguous text under **Check before you write it in**, and starts medications/allergies, exam, studies,
  assessment/plan, orders, time and attestation unchecked until the provider explicitly confirms them.
- **Or just type.** Type the story in Story the quickest way (fragments, abbreviations, half-finished thoughts), or
  paste notes or a visit transcript into **Type or paste** on Build. Build reads it the way a careful scribe would:
  it lists the boxes the text says plainly for you to tick one by one (each with the words that ticked it), and puts
  anything hedged ("maybe hearing loss"), contradicted ("denies weakness but later says grip feels weaker") or
  unfinished under **Check before you write it in** instead of ticking it.
- **Meds** every visit: allergies, the medication list from the last note as rows (search 800+ drugs by name or brand,
  dose and how often from the drug's strengths, Taking / New / Changed / Not taking / D/C), an allergy warning when a
  drug matches an allergy, which drugs are held before VNG, ototoxic drugs, medications the story mentions, and
  **Mark reviewed with patient**, which adds the clinic's review statement. Build reminds you until it's done.
- **Dates** take a calendar or typing ("8/15/26", "081526", "Aug 15 2026", "today"); **age** is worked out from the
  date of birth and the date of service.
- **Billing** opens the office app (Billing requests, Superbills, Balances, MIPS, Dashboard); **Settings** holds the
  folders, dictation and clinic data. Find any patient from the search box (Ctrl+K on Windows, Cmd+K on a Mac).
- **Sign-in and permissions:** local accounts belong to groups (Administrators, Providers, Clinical Staff, Front Desk
  and Billing, plus groups an administrator creates). Passwords are salted and hashed in the local app-data folder;
  the server checks permissions for schedules, patients, notes, filing, billing, settings and user administration.
  The first-run administrator is `admin` / `change-me-now` and is marked for an immediate password change in **Users & groups**. Existing installations keep their current local accounts.

`--classic` opens the office app on its own; `--demo` opens made-up patients only. Everything runs on the clinic's
own PCs and file server.

The sections below describe the office tabs and windows.

## Dashboard, Superbills, Balances

The office window opens on the **Dashboard** (or on the tab picked in the top bar): one click to All patients, Today, the Auth worklist, Superbills and Balances, plus a card for each part of the clinic map (patients, Outlook bookings, drive files, superbills placed, Veradigm balances, the next clinic day's alerts).

- **Superbills** maps every scanned batch in the superbill folder to its patients: each page is read once (in the background, several at a time) and placed using that day's schedule. It can be stopped any time and picks up where it left off. Charts then show each visit's superbill page at once, and "Find" lists a patient's superbills by last name + DOB.
- **Build manual review packet…** on the Superbills tab turns selected daily batches into a consistent, page-by-page pre-upload queue plus a fill-in CSV worklist. It uses local OCR only to split the batch and offer high-confidence schedule matches; it does not interpret handwriting or pen marks and does not draw automatic finding boxes. The source scans are never changed.
- **Balances** reads Veradigm's *Revenue Detail Report* (Summarize by Voucher) PDFs saved in Desktop\ALL. It shows each visit still open (insurance and patient together), aged by date of service, with the patient's own balance from the last Financial Inquiry run. The **Name check** view lists Veradigm patient numbers whose name differs from the Outlook headers, or that no header carries.

## MIPS 2026

Reads the office's own measure workbooks in `V:\MIPS 2024\MIPS\MIPS 2026` (the files uploaded to MD Interactive; setting `mips_folder`) and shows, for each quality measure (#126 diabetic neuro exam, #268 epilepsy counseling, #277 sleep-apnea severity, #291 Parkinson's cognitive assessment, #293 Parkinson's therapy referral):

- the 2026 rules: who counts, what meets the measure, how the office fills it in, the workbook's choices - from the office's MIPS instructions and this year's decisions (2026 visits only; #268 needs both counseling boxes at every visit);
- where the office stands: rows, Done / with a reason / Not done / blank, **data completeness** against the 75% minimum, the performance rate, and the 20-case minimum;
- **who still needs something this year**, with their next visit: blank rows, rows marked "Done?", and a "Not Done" on a once-a-year measure (a later visit this year that documents it still counts).

The same reminders show on those patients' appointments in **Today** ("MIPS 291 MoCA", "MIPS 268 both boxes"...), so it gets done at the visit. The app never writes to the workbooks.

## Billing worklist

1. In Teams, click in the billing chat, press **Ctrl+A**, then **Ctrl+C**.
2. In the app, click **Paste requests from Teams** (or press Ctrl+Shift+V).
3. The app reads every message and pulls out the patient name, account #, DOB, DOS, CPT codes, payer and the request type: medical records, appeal or downcode, denial, missing auth, missing superbill or "was pt seen", provider not circled, insurance update, address, refund, coding question, call back.
4. Click a request. The app searches the drive by account #, name and DOS and ranks the matching files. When the request has a date of service, **that visit's superbill page and progress note are attached automatically** at the top of the list (blue rows). The superbill is pulled out of the day's scanned batch as its own PDF, named `Last First CODE mm.dd.yy SB.pdf`.
5. Click **Copy to Missing Folder**. The app copies the files for billing, puts a reply on the clipboard ("M/R for … uploaded to the Missing Folder. *AB") and marks the request done.

A request is marked **Answered** automatically when someone on our side quote-replies to it in Teams. Pasting the same chat again doesn't create duplicates, and each request keeps its status (Open, Answered or Done).

## Patient record

Type a name, account # or DOB in **Find patient** and press Enter (or click **Open record**). The record window shows:

- **Every visit**, from all Outlook calendars (booked, seen, no-show, upcoming) and from the progress notes on the drive. Selecting a visit finds and attaches its superbill page. It also shows the note's key fields: visit type, total time, attending attestation, G2211 statement and the numbered impressions.
- **Every document**: progress notes and prepped notes, the extracted superbills, and every file on the indexed drives that matches the patient (faxes, referrals and auths, EMG/VNG reports, cards).
- **Warnings**, for example when Outlook holds two different DOBs under the same name, or when a note's Date field doesn't match its visit.

Outlook is copied into a local cache (read-only; the whole history back to 2006, about 320,000 bookings, takes under a minute, and after that only changes are read), so a patient's full booking history appears instantly.

## Calendar

Calendar shows the schedule by provider in **Day**, **Week** and **Month** views. Each provider has a stable color and
can be checked or unchecked; text and appointment-type filters narrow the visible schedule. A patient copied onto more
than one provider calendar at the same time is striped and labeled **Across N schedules**, so the copies are visible
without looking like separate unexplained visits. Use ◀, Today and ▶ to move through the selected range.

Each visit shows:

- **Authorization.** For plans that need one (SCMG, Sharp, Scripps, Health Net, HMOs), whether an auth written in the patient's appointment notes covers the visit: OK, Ends soon, No auth, Expired, Starts after visit, or Test not on auth. The last one means a VNG, EEG or EMG booked on an auth that lists only office-visit codes.
- **Notes** such as "Need Chair Auth".
- **Balance.** The latest Veradigm self balance, credit, and anything over 90 days.
- **ABN form**, **STAT**, and **Check header** (a header with "??" or no plan).
- **Verify on.** The portal to check this plan's eligibility on: Sharp Link, Scripps Care Link, Noridian, Availity, UHC, Cigna, Tricare and so on.
- **Eligibility.** The latest check on file. Right-click a visit to record one (Eligible, Inactive, Problem with a note), stamped with your initials. Checks made by the clinic's terminal Claude in the payer portals are imported automatically from `ClaudeRoutines\eligibility\elig_*.jsonl`.

Buttons:

- **Copy chart-pull list:** the day's patients per calendar, in appointment order, for pulling charts.
- **Copy eligibility list:** the day's patients grouped by portal, with plan and member ID.

## Auth worklist

The auth worklist covers the coming visits (from the next clinic day, 14 days by default) on plans that need an authorization. The problems come first: No auth, Expired, Starts after visit, Test not on auth, Ends soon. Each row shows the auth's number, dates, codes and units, and any auth notes.

Double-click a row to open the patient's chart on the Authorizations tab. **Copy list** copies the worklist to the clipboard for the referral coordinator.

The first check of 14 days reads the appointment notes from Outlook, which takes about 10–15 s. After that it's cached, and each note is read again only when its appointment changes.

## All patients

Click **All patients** for the practice's patient directory: everyone ever booked in Outlook (about 43,000 patients), plus people who only appear in file names (seen before the Outlook history starts). Type a name (`smith`, `Smith, J`, `john smith`), a DOB (`1/2/1950`) or a Pt #, and the list filters as you type. Double-click a patient to open their record.

The directory comes with a **map of the drive**: every file whose name (or folder name) names a patient is linked to that patient. It understands the office's naming styles:

- `Last, First 09 24 26`
- `LastFirst 09 24 26`
- `LastF 09 24 26`, with the first initial only
- `LastFirstM`, with a middle initial
- fax labels such as `TY Last First Sender …`
- per-patient folders

When two patients share a name, a DOB or Pt # in the path, or a visit on the file's date, decides which one it is. If none of those can decide, the file shows under both, marked **same name**. A first name spelled differently from Outlook (Jon/John) is linked only when that patient had a visit on the file's date.

The map rebuilds itself after each drive scan or Outlook sync, in about 10 seconds for 700,000 files. It reads file names only, never file contents. Staff folders (HR, Applicants, Payroll) are never linked.

## How the superbill is found

Each day's superbills are scanned per provider (`EC 09.24.26.pdf`). Every superbill is followed by that patient's own pages (cards, referrals, forms). To find a patient's page, the app:

1. Takes the provider's seen patients that day from Outlook, in appointment order.
2. Reads the batch with Windows' built-in OCR (cached).
3. Lines the whole day up against the batch. A packet whose printed pages carry a patient's full name or DOB anchors the order; everyone else is placed in order between the anchors. Patients booked at the same time can be scanned in either order, so their insurance cards decide. A booked patient with no superbill and no progress note (usually an untagged no-show) is skipped instead of shifting everyone after them.

A result is marked **sure** only when printed pages identify the patient, or when their place is pinned by identified neighbours. Otherwise it says **CHECK** and why (for example "booked at the same time as X and nothing printed tells them apart"). On a test day of 53 superbills, every "sure" answer was right.

## Superbill fixes ("PROVIDER NOT CIRCLED")

For a superbill request, select the request (or a visit in the patient record) and click **Superbill…**. The app:

1. Reads the Outlook schedule for the date of service.
2. Finds the patient's superbill as above.
3. Suggests the rendering provider and the attending physician, with the reasons. The suggestion follows the rules in settings:
   - tags such as "DR has to be present"
   - payers a physician can't see
   - who was out that day
   - the physician named on the referral or authorization
   - who had patients that day
4. Shows the page. You check it, change anything, then click **Amend && file**.

"Amend & file" writes a **new one-page PDF**; the original batch is never changed. The new page has red marks:

- the rendering provider circled
- a circled X in the attending's column
- optionally, the Medicare incident-to answer
- corrected DOB/DOS values written above their boxes
- a stamp: `AMENDED <date time> *<initials>`, provider/attending, the schedule evidence, account and DOS, "Processed via DizzyDoctor"

The stamp goes in the blank space under the PROVIDER box. Long lines wrap and the text gets smaller to fit there. It never goes over the patient name / DOB / DOS boxes at the top: if it can't fit, the page shows "Can't mark this page" instead. If OCR can't read the provider's own row (their pen stroke runs through the name) but reads the rows around it, the row is placed by the roster's order. Check the preview. The roster in `config.json` lists providers in the order the superbill prints them.

The copy is saved to the **office folder** (for example `M:\1 MPS`) and, if one is set, the **billing folder**, both named `Last First CODE mm.dd.yy SB.pdf`. Every amendment is logged, and the request is marked Done once billing has its copy.

The provider roster, the Outlook calendar names and the attending rules live in `config.json`. See `config.example.json`.

## Claude

- **Work it.** Claude works the selected request end to end:
  1. It works out what billing needs.
  2. It checks the Outlook schedule for the visit.
  3. It searches the drive, lists the progress notes, and finds the superbill.
  4. It **reads** the documents and checks them against the request.
  5. It finishes with the evidence (document and page), a reply for billing, and any appeal letter.

  Copying files to the Missing Folder and adding playbook rules wait for someone to click **Approve**.
- **Reading chats.** Pasted Teams requests get a summary, a list of anything missing, and a suggested reply.
- **Ask.** A back-and-forth conversation about one case, with documents attached.
- **Draft.** A reply to billing, an appeal letter, or a plain-language explanation.
- **Playbook.** The clinic's rules, people and procedures, used on every request. **Work it** suggests new rules as it learns them, and a person approves each one. Never put patient information in the playbook.

Setup: in **Settings → Claude**, paste the API key from the [Claude Console](https://platform.claude.com) and click **Test**. Saving the key the first time asks you to confirm that the practice's BAA with Anthropic is signed and HIPAA is enabled.

How it works: the lookups run on this PC, and Claude only sees what it asks for. Everything goes through the plain Messages API, which the BAA covers. The app uses no beta features: a HIPAA-configured organization rejects anything that isn't on Anthropic's HIPAA list. The Files, Skills, Batch and Agents features aren't used either. The key is stored in Windows Credential Manager (the Keychain on a Mac). Long runs are prompt-cached, so the repeated parts cost about a tenth as much.

Cost:

- **Cheaper model for routine work.** Reading chats, drafts and the Settings test use Claude Sonnet 5.5, which costs half as much. **Work it** and **Ask** use Claude Opus 5.5.
- **Work it has limits.** It stops after 12 steps and reads at most 4 pages at a time.
- **Daily limit.** Set it in **Settings → Claude**; the default is $5, and 0 means no limit. The status bar shows today's spend after each Claude call. Once the limit is reached, Claude features stop until tomorrow, and pasted chats are read by the built-in rules instead.
- **A hard monthly cap.** For that, also set a spend limit on the workspace in the Claude Console.

For development, `recordfinder/demo.py` creates a made-up clinic (fake patients, EOB, notes and chat) that the tests use.

## Mac

The app runs on macOS too; Outlook and Windows OCR are Windows-only, so superbill lookup needs a PC.
`bash build-mac.sh install` builds `DizzyDoctor.app`, runs the tests and the app's self-test, and
copies it to `~/Applications`.

## Privacy

- Without a Claude key, nothing is sent over the internet. With one, what Claude reads goes to Anthropic under the practice's BAA. Either way, the app only copies files into the folders you choose.
- Each PC keeps its data in `%LOCALAPPDATA%\DizzyDoctor\index.db` and its settings in `%APPDATA%\DizzyDoctor\config.json`. Existing folders from the former app name are moved here automatically on first start. The database holds:
  - the drive index (file names, paths and dates)
  - the Outlook cache and the patient directory
  - Veradigm balances and the superbill map
  - OCR text of scanned superbill batches that have been looked up
  - key fields read from progress notes that have been opened

  With the clinic-wide install, the main PC also puts a copy of that database on the clinic's file server (the `data` folder of the install folder, next to the patient records it describes) so the other PCs can load it. A PC's own billing requests, amendment log and AI readings never leave it. Keep BitLocker on for every PC.
- The app never writes to Outlook and never posts to Teams. Replies go on your clipboard, and you paste them yourself.
- `DizzyDoctor.exe --selftest result.json` checks OCR and Outlook access on a PC. It writes only yes/no answers and counts.

## Install on every clinic PC

One PC is the **main PC**: the one with the staff Outlook, the Veradigm report downloads (Desktop\ALL) and the superbill mapper. Its settings have `"publish_data": true` and `"share_folder"` pointing at the install folder on the file server, e.g. `M:\DizzyDoctor\App`. It scans the drive, reads Outlook and Veradigm, maps patients and superbills, and refreshes this every hour while it's open. Whenever the data changed, it publishes a copy to the server (at most hourly).

Every other PC:

1. Open the install folder on the file server and double-click the installer. No admin rights are needed. It copies the app to `%LOCALAPPDATA%\Programs\DizzyDoctor` with the office `config.json`, adds Desktop and Start-menu shortcuts, and starts it.
2. At every start, the app loads the newest published data (the first time, a few hundred MB). It doesn't scan the drive or read Outlook itself.
3. While it's open, it checks for newer data every 20 minutes and offers to reload. The Dashboard's **Check for new data** does it right away.
4. When a newer version is in the install folder, the app updates itself at its next start. If the server copy is not newer, an installed Windows copy also checks the public GitHub Release. Every downloaded executable is checked against its published SHA-256 digest before it replaces the running version.

**Publishing a new version:** build it (below), then run

```
python deploy\publish_release.py "M:\DizzyDoctor\App" dist\DizzyDoctor.exe --office-config-from "%APPDATA%\DizzyDoctor\config.json"
```

It copies the exe, `version.json`, the installer and the read-me. With `--office-config-from` it also writes the office `config.json`: the main PC's settings without anything personal or PC-specific (initials, signature, publishing, AI settings), plus `share_folder`.

A single PC without the server can still run `DizzyDoctor.exe` on its own: leave `share_folder` empty and it scans and reads everything itself, as before.

## Building the .exe

Both builds come from the same code and show the same screens, name and icon. The Windows build
carries the DizzyDoctor icon (Explorer, taskbar, Start menu, title bar) and a version resource, so
Properties and Task Manager show "DizzyDoctor" and the version.

- **Without Windows:** push this repo to GitHub. The *Build Windows exe* action runs the tests,
  builds `DizzyDoctor.exe`, starts it once to check that it opens and fills a note, and offers it as
  a download (*DizzyDoctor* artifact: the exe and `Install DizzyDoctor.cmd`).
- **On a Windows PC:** install Python 3.10+ from python.org, then double-click `build.bat`. It
  installs `requirements.txt`, runs the tests and writes `dist\DizzyDoctor.exe`.

Then publish it to the install folder (above) with `deploy\publish_release.py`. PCs that still run
the older `DizzyDoctorOS.exe` update themselves to it, and their settings carry over.

## Development

```
python -m pip install -r requirements.txt
python -m unittest -v     # parser, matcher, superbill and record tests (synthetic data only)
python main.py            # run the app
python main.py --update-only  # safely fast-forward a clean source checkout, then exit
```

Clean source checkouts check their current upstream branch whenever the app opens, fast-forward only, and restart before loading the app. Local code changes are never overwritten. Never commit real chat exports or patient data; generated output, databases, imports, exports, CSV files, and logs are ignored. Test fixtures use unmistakably synthetic names and numbers.

## Charting (progress notes)

**Note** is the clinic's own progress note - the fillable PDF the office files today - as a screen:
the patient in one header row (name in bold, DOB, age, Pt #, visit, allergy and history tags, the
visit clock and the visit level), the note's steps down the left, and the finished note filed to the
Progress Notes folder as `LastFirst mm dd yy.pdf`, exactly as before. Name, DOB and date print in the
form's own Helvetica Bold.

- **New visit from last note.** History, family and social history, medications, allergies, studies,
  labs and the numbered impressions carry over; the HPI opens with the practice's follow-up
  sentence; symptoms, exam, time and attestation start fresh.
- **Last-visit reference for new visits.** Starting from the last note also shows that visit grayed out at the top
  of the Patient step — reason, assessment, studies, meds and the booked follow-up — with the follow-ups it left
  behind (assessment lines, pending studies, referrals, the booked return) as a tick-off list. Ticking Addressed or
  **Pull into plan** moves only what was handled into today's plan box; the gray reference itself never files. Open
  items also remind on the Plan step.
- **Filed history is read-only.** Every reconciled progress note from every configured year is mapped into the
  patient's visit history using the patient map (name, DOB, account and unique visit date). Filed notes open in the
  PDF viewer with a read-only banner; only drafts enter the editor. The Notes on file list shows the complete history,
  not just the most recent dozen visits.
- **Phrases.** Every text box offers the wording the practice already uses (interim lines, pertinent
  negatives, exam findings, plan sentences, time statements), filled in with the patient's details.
- **Dictation.** Press **Dictate** (or F9) and talk. Speech is transcribed on this computer with
  Whisper - nothing leaves the PC. "Dizziness, worse with standing, lasts seconds, paroxysmal" ticks
  the boxes; "blood pressure 128 over 76" fills vitals; "exam otherwise normal, mild sway on Romberg"
  marks the exam rows in the practice's own wording; "impression one dizziness and lack of
  coordination dash monitor clinically" writes the numbered impression; anything else goes into the
  text box you were in. A whole visit can be one dictation: "next section exam", "plan:" or
  "impression one" moves on, and "build the note" ends it. Undo is one click. The first use downloads
  the model (~460 MB).
- **Typed notes and pasted transcripts.** `charting/story.py` reads the provider's own notes (typed into Story or
  pasted on Build): a symptom is ticked present or denied only where the text says so plainly; details (worse with
  standing, AS, paroxysmal) only inside a sentence about that symptom; hedged, contradicted and unfinished points
  are listed to check; someone else's history ("father had tremor") is never the patient's. A transcript (speaker
  labels or plain text) is read in `charting/scribe.py`: answered questions ("Any ringing?" "No."), the patient's and
  a companion's own words, and the provider's summaries; plans and instructions are not history. Each box comes with
  the words that ticked it.
- **Medications.** The note keeps them as text in its one box (`--------PO Medications--------`, then
  `Gabapentin 300mg TID (new)` lines); Meds shows that text as rows and writes it back the same way, leaving untouched
  lines exactly as they were. The next visit starts from the list with stopped drugs dropped and the review statement
  taken off. The drug list (`charting/web/meds.json`) is offline and was cross-checked twice per drug class.
- **Build note.** Writes the note out from the boxes ticked in the exam room and what was dictated,
  in the practice's wording (`charting/phrases.py`): the HPI (the clinic's opening line, each symptom
  written out, pertinent negatives, the closing line), the normal wording on exam rows marked normal,
  numbered impressions with the plan boxes that were ticked, and the chief complaint. Each part is
  shown next to what the note says now, to apply, edit or skip; the provider's own text is kept, and
  a rebuild replaces only what the builder wrote. It never writes a maneuver, minutes, an abnormal
  finding or an unticked plan: those stay `***` or are listed as still to write.
- **Billing.** The visit level from the documented total time (2021 office-visit rules), prolonged
  service, G2211, modifier 25, the procedures behind each separate-time statement, ICD-10 for the
  numbered impressions, and the checks a coder makes (a procedure on page 3 with no time statement on
  page 4, an incident-to attestation on a new patient, a medication discussion with no drug named).
  Suggestions only; items marked *verify* should be confirmed by the practice's coder.
- **Preview** renders the note as it will file.

Settings: `notes_folders` (final notes, year subfolders), `prepped_notes_folder` (drafts),
`note_template` (a revised blank form), `dictation_model` (`tiny.en` … `medium.en`).
The form's field map is `recordfinder/charting/note_schema.json`, rebuilt from the blank PDF with
`python -m recordfinder.charting.build_schema`.

## PaperEpley Fax Slayer & Offline Clinical AI (`cranial_viii/`)

Integrated into DizzyDoctor for completely offline, sovereign clinical AI and automated inbound fax routing with zero third-party cloud dependencies:

- **PaperEpley Fax Slayer** (`cranial_viii/services/fax_slayer`): Polls Microsoft Graph API and network watch folders (`V:\Faxes\Inbound`), automatically matches and renames inbound faxes with 93.5% confidence, and routes the remaining 2.3% discrepancies into a rapid side-by-side human review queue.
- **Sovereign Offline AI** (`cranial_viii/services/clinical_ai`): Local LLM integration (Ollama / llama.cpp) running Qwen-2.5-14B / Llama-3.1-8B for BPPV canal localization & maneuver planning, HINTS stroke vs. neuritis triage, and Meniere's vs. vestibular migraine differentials.
- **Clinical Database & Vector Store** (`cranial_viii/core/db`): PostgreSQL 16 schema with partial bounded btree indexes and `pgvector` clinical embeddings for document retrieval.
- **Clinical Worklist & SLA Monitor** (`cranial_viii/services/workflow`): Enforces clinical stage transitions and flags overdue unsigned documentation.
- **Playbooks** (`playbooks/`): Front office procedures for superbill pre-upload review, provider-not-circled edge cases, and insurance acceptance rules.
