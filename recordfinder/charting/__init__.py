"""Charting: the practice's progress note as a form the app fills, reads and files.

The note is the clinic's own fillable PDF (templates/progress-note-2025.pdf). note_schema.json
describes every field on it (built by build_schema.py); note.py fills and reads it; coding.py
turns a finished note into billing suggestions; dictation.py transcribes speech on this
computer; server.py serves the chart screen to the app's window.
"""
