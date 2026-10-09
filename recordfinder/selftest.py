"""`DizzyDoctor.exe --selftest result.json` - checks this PC/build can read PDFs, run OCR and
reach Outlook. Writes only yes/no answers and counts (no patient data)."""
from __future__ import annotations

import json


def run(out_path: str) -> dict:
    res: dict = {}
    try:
        import pymupdf
        from . import ocr
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 120), "SELFTEST 12345", fontsize=28)
        res["ocr_unavailable"] = ocr.unavailable_reason()
        res["ocr_text"] = "" if res["ocr_unavailable"] else ocr.text_of(ocr.ocr_page(page))
    except Exception as ex:
        res["ocr_error"] = repr(ex)
    try:
        from . import schedule
        res["outlook_unavailable"] = schedule.outlook_unavailable_reason()
        if not res["outlook_unavailable"]:
            folders = schedule.calendar_folders(schedule._outlook())
            res["outlook_calendars"] = len(folders)
            # The fast bulk read (Outlook Table) on the biggest calendar, one year: counts and timing only.
            import time
            from datetime import date
            from . import outlook_cache
            name, folder = max(((k, v) for k, v in folders.items() if k != "Birthdays"), key=lambda kv: kv[1].Items.Count)
            y = date.today().year
            flt = f"[Start] >= '01/01/{y} 12:00 AM' AND [Start] < '01/01/{y + 1} 12:00 AM'"
            rows, t = [], time.monotonic()
            got = outlook_cache._read_table(name, folder, flt, rows, lambda: None, None)
            res["outlook_fast_read"] = got is not None
            res["outlook_fast_read_rows"] = got or 0
            res["outlook_fast_read_seconds"] = round(time.monotonic() - t, 2)
    except Exception as ex:
        res["outlook_error"] = repr(ex)
    try:
        # charting: the note form fills, and the local dictation engine is bundled
        import os
        import tempfile
        from .charting import dictation, note
        tmp = os.path.join(tempfile.gettempdir(), "ddos-selftest-note.pdf")
        note.fill(dict(note.blank(), Name="Self, Test", Date="01/01/2026"), tmp)
        res["note_fields"] = len(note.read(tmp))
        os.remove(tmp)
        res["dictation_available"] = dictation.available()
    except Exception as ex:
        res["charting_error"] = repr(ex)
    try:
        # the MIPS workbooks (.xlsx reader bundled, V: reachable): rows per measure only
        from . import config, mips
        mips.FOLDER = config.load().get("mips_folder") or mips.DEFAULT_FOLDER
        res["mips_rows"] = {s.measure.number: s.rows for s in mips.status()}
    except Exception as ex:
        res["mips_error"] = repr(ex)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    return res
