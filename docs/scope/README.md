# Scope → DizzyDoctorOS

These are the versioned Scope templates consumed by **Build → Scope note** in DizzyDoctorOS.

## Install in Scope

1. In Scope, open **Templates → New template → Create from scratch**.
2. Copy one template file below into the editor and use the filename's title.
3. Keep the `DIZZYDOCTOR SCOPE TRANSFER V1` line and every section heading unchanged.
4. Save and, if appropriate, share it with the practice organization.

Templates:

- `DizzyDoctor - New Consultation.md`
- `DizzyDoctor - Neurologic Evaluation.md`
- `DizzyDoctor - Progress Follow-Up.md`
- `DizzyDoctor - Urgent Visit.md`

## At the visit

1. Pick the matching Scope template and record the encounter.
2. Before stopping, dictate the abnormal exam findings, assessment, medication changes, orders,
   follow-up and encounter time. Do not rely on Scope to infer them from context.
3. Review Scope's `NEEDS REVIEW` section, then use **Copy text**.
4. In DizzyDoctorOS open **Build**, paste, choose **Scope note**, and click **Read it**.
5. Scope-derived clinical boxes and all high-risk fields remain unchecked until the clinician confirms them.

The import is intentionally strict. Studies must use `Study | Date | Result`; referrals must use
`Clinician | Specialty | Diagnosis`; imaging orders must match a choice on the progress note. Anything
else is shown under **Check before you write it in** rather than guessed.
