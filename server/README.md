# Server pipeline (moving in)

This folder is the home for the clinic-wide pipeline that was started on the admin PC (`V:\11111111IMP\SW\DizzyDoctorOS`):

- **ingest/**: crawls the clinic drive (`SOURCE_ROOT`, e.g. `V:\`) into Postgres, classifies documents, extracts DOS and DOB, and repairs dates.
- **Postgres schema**: 17 tables owned by the least-privilege `dizzy` role.
- **Encryption:** a key-encryption key (`KEK_PATH`), with **session auth**, plus mailbox reading through Microsoft Graph.

Plan:

1. Move the pipeline code here and commit it. `.env`, `keys/`, `*.kek` and any data are git-ignored, and **must never be committed**. Ship a `.env.example` with names only.
2. Finish the initial load on the admin PC (the loader resumes), then move Postgres to **purcellfileserv** for go-live.
3. Rotate the `dizzy` password: it was printed into a synced transcript. Do it without printing the new value.
4. Point the desktop app (`recordfinder/`) at the database for drive lookups, in place of its own file-name index. Claude's **Work it** tools then search the full classified corpus.
