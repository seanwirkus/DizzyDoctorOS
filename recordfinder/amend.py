"""Make an amended copy of one superbill page, marked so everyone can tell what changed.

Marks are drawn in red so billing can tell them apart from the provider's own ink:
  - an ellipse around the rendering provider in the PROVIDER list
  - a circled X in the attending physician's column on that row
  - optionally, the YES / NO cell of the "MEDICARE INCIDENT-TO MET" row circled
  - a stamp: what was amended, when, by whom (initials), from what evidence
The PDF also carries "DizzyDoctor;amended" keywords so the app can recognize its own
amendments later. The original batch PDF is never modified.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher

from . import ocr

RED = (0.85, 0, 0)
KEYWORD = "DizzyDoctor;amended"
LEGACY_KEYWORDS = ("dizzdocOs;amended", "ClinicRecordFinder;amended")   # copies stamped under earlier names


def _alnum(t: str) -> str:
    return "".join(ch for ch in t.upper() if ch.isalnum())


def similar(a: str, b: str) -> float:
    # OCR on these forms swaps M/H/K/N and drops punctuation, so compare loosely.
    return SequenceMatcher(None, _alnum(a), _alnum(b)).ratio()


@dataclass
class Stamp:
    initials: str
    provider: str
    attending: str = ""
    evidence: str = ""          # e.g. "Outlook sched 9/24 10:00 (Alex Brown)"
    account: str = ""
    dos: str = ""               # "09/24/2026"
    incident_to: str = ""       # "YES" / "NO" when the Medicare incident-to row was answered
    extra: list[str] = field(default_factory=list)

    def lines(self, now: datetime) -> list[str]:
        out = [f"AMENDED {now:%m/%d/%Y %I:%M %p}" + (f"  *{self.initials.lstrip('*')}" if self.initials else ""),
               f"Provider: {self.provider}"]
        if self.attending:
            out.append(f"Attending: {self.attending}")
        if self.incident_to:
            out.append(f"Medicare incident-to: {self.incident_to}")
        out += self.extra
        if self.evidence:
            out.append(f"Per {self.evidence}")
        ids = "  ".join(x for x in (f"Acct {self.account}" if self.account else "", f"DOS {self.dos}" if self.dos else "") if x)
        if ids:
            out.append(ids)
        out.append("Processed via DizzyDoctor")
        return out


class LayoutError(RuntimeError):
    """The page doesn't look like the expected superbill (provider list not found, etc.)."""


def _circled_x(page, cx: float, cy: float, r: float = 5.2):
    import pymupdf
    c = pymupdf.Point(cx, cy)
    page.draw_circle(c, r, color=RED, width=1.4)
    d = r * 0.62
    page.draw_line(c + (-d, -d), c + (d, d), color=RED, width=1.4)
    page.draw_line(c + (-d, d), c + (d, -d), color=RED, width=1.4)


def _row_of(in_list: list, word):
    """The whole printed row a word of the PROVIDER list sits on ("ALEX BROWN, PA-C")."""
    import pymupdf
    row = pymupdf.Rect(word.x0, word.y0, word.x1, word.y1)
    for w in in_list:
        if abs(w.y0 - word.y0) < 3:
            row |= pymupdf.Rect(w.x0, w.y0, w.x1, w.y1)
    return row


def _infer_row(in_list: list, provider_surname: str, roster_surnames: list[str]):
    """Where the provider's row must be when OCR read nothing on it (their pen stroke runs through the name).

    The roster lists providers in the order the form prints them, so the row is placed by its position in
    the roster between the rows OCR did read, at their row pitch. It is as wide as the list as read (its widest
    name), not as the rows next to it, which may hold shorter names. Returns (row, extra): extra is how much
    wider this name may be than the widest one read, from the letters its surname has beyond the longest
    surname read (0 when it has none). None when that can't be done safely: the provider isn't on the roster, fewer than two other rows were read,
    or the rows read aren't evenly spaced in roster order (a roster that isn't in printed order)."""
    import pymupdf
    order = [_alnum(s) for s in roster_surnames]
    if _alnum(provider_surname) not in order:
        return None
    k = order.index(_alnum(provider_surname))
    found, read = {}, {}                         # roster position -> row rect, surname word
    for i, s in enumerate(roster_surnames):
        if i == k or len(order[i]) < 4:
            continue
        best = max(in_list, key=lambda w: similar(w.text, s), default=None)
        if best is not None and similar(best.text, s) >= 0.72:
            found[i], read[i] = _row_of(in_list, best), best
    idx = sorted(found)
    if len(idx) < 2 or any(found[b].y0 - found[a].y0 < 3 for a, b in zip(idx, idx[1:])):
        return None
    steps = sorted((found[b].y0 - found[a].y0) / (b - a) for a, b in zip(idx, idx[1:]))
    pitch = steps[len(steps) // 2]
    if any(abs(s - pitch) > 0.2 * pitch for s in steps):
        return None
    above, below = [i for i in idx if i < k], [i for i in idx if i > k]
    if above and below:                          # between two rows read: interpolate
        a, b = above[-1], below[0]
        y0 = found[a].y0 + (found[b].y0 - found[a].y0) * (k - a) / (b - a)
    else:                                        # first or last row: one or two pitches past the nearest row
        j = above[-1] if above else below[0]
        if abs(k - j) > 2:
            return None
        y0 = found[j].y0 + (k - j) * pitch
    heights = sorted(r.height for r in found.values())
    row = pymupdf.Rect(min(r.x0 for r in found.values()), y0,
                       max(r.x1 for r in found.values()), y0 + heights[len(heights) // 2])
    per_letter = sorted((w.x1 - w.x0) / max(len(_alnum(w.text)), 1) for w in read.values())
    more = len(order[k]) - max(len(order[i]) for i in found)
    return row, max(more, 0) * per_letter[len(per_letter) // 2]


def locate(words: list, page_w: float, page_h: float, provider_surname: str, roster_surnames: list[str]) -> dict:
    """Find the PROVIDER list, the provider's row and the attending columns from OCR words.

    If OCR read the rest of the list but not the provider's own row, the row is inferred from the roster order
    (the result's "inferred" is True then)."""
    import pymupdf
    region = [w for w in words if w.y0 > page_h * 0.55 and w.x0 > page_w * 0.55]
    listed = [w for w in region if any(similar(w.text, s) >= 0.72 for s in roster_surnames if len(s) >= 4)]
    if not listed:
        raise LayoutError("couldn't find the PROVIDER list on this page")
    first = min(listed, key=lambda w: w.y0)                   # top row of the list
    left_edge = min(w.x0 for w in listed) - 30
    right_edge = max(w.x1 for w in listed) + 30
    in_list = [w for w in region if w.y0 >= first.y0 - 1 and left_edge - 10 < w.x0 < right_edge]
    best = max(in_list, key=lambda w: similar(w.text, provider_surname), default=None)
    inferred = best is None or similar(best.text, provider_surname) < 0.7
    extra = 0.0
    if inferred:
        guess = _infer_row(in_list, provider_surname, roster_surnames)
        if guess is None:
            raise LayoutError(f"couldn't find {provider_surname} in the PROVIDER list")
        row, extra = guess
        if row.y0 < first.y0:                                 # the unread row is the top one
            first = ocr.Word(provider_surname, row.x0, row.y0, row.x1, row.y1)
    else:
        row = _row_of(in_list, best)
    # Columns flank the list; their headers sit on the row above the first provider, and the
    # YES / NO cells further down share the same x positions.
    left_cells = [w for w in region if w.x1 < left_edge and w.x0 > left_edge - 80 and
                  ((-16 < w.y0 - first.y0 < -3 and len(_alnum(w.text)) <= 4) or (w.y0 > row.y1 and similar(w.text, "YES") >= 0.66))]
    right_cells = [w for w in region if w.x0 > right_edge and
                   ((-16 < w.y0 - first.y0 < -3 and len(_alnum(w.text)) <= 4) or (w.y0 > row.y1 and similar(w.text, "NO") >= 0.5))]
    col_x = {}
    if left_cells:
        col_x["left"] = sum(w.cx for w in left_cells) / len(left_cells)
    if right_cells:
        col_x["right"] = sum(w.cx for w in right_cells) / len(right_cells)
    headers = {_alnum(w.text): w.cx for w in left_cells + right_cells if -16 < w.y0 - first.y0 < -3}
    if extra:
        # A longer surname than any OCR read: widen by the extra letters (the list is centred), but stay inside
        # the PROVIDER column, clear of the attending columns' circled X (the oval adds 9 pt each side).
        lo = col_x["left"] + 20 if "left" in col_x else left_edge
        hi = col_x["right"] - 20 if "right" in col_x else right_edge
        row = pymupdf.Rect(max(lo, row.x0 - extra / 2), row.y0, min(hi, row.x1 + extra / 2), row.y1)
    medicare = next((w for w in region if w.y0 > row.y1 and similar(w.text, "MEDICARE") >= 0.7), None)
    return {"first": first, "row": row, "col_x": col_x, "headers": headers, "medicare": medicare,
            "left_edge": left_edge, "region": region, "inferred": inferred}


def _try_locate(words, W, H, surname, roster_surnames):
    try:
        return locate(words, W, H, surname, roster_surnames)
    except LayoutError:
        return None


def column_x(layout: dict, column: str, side: str) -> float:
    """x of an attending column: by its header text if OCR read it, else by side ("left"/"right")."""
    want = _alnum(column)
    for text, x in layout["headers"].items():
        if want and similar(text, want) >= 0.66:
            return x
    if side in layout["col_x"]:
        return layout["col_x"][side]
    raise LayoutError(f"couldn't find the {column or side} column")


HEADER_ANCHORS = {"DOB": "BIRTH", "DOS": "SERVICE"}


def write_corrections(page, words: list, corrections: dict) -> dict:
    """Write corrected header values in red, in the title bar of their box ("DATE OF BIRTH ->01/28/1951").

    The provider's handwriting stays visible; the stamp says where the corrected value came from."""
    import pymupdf
    W, H = page.rect.width, page.rect.height
    placed = {}
    for field, value in corrections.items():
        anchor = HEADER_ANCHORS.get(field)
        hits = [w for w in words if w.y0 < H * 0.15 and similar(w.text, anchor) >= 0.75] if anchor else []
        if not hits:
            raise LayoutError(f"couldn't find the {field} box")
        a = min(hits, key=lambda w: w.y0)
        # The whole printed title ("DATE OF BIRTH"): words on the anchor's line just left of it.
        title = pymupdf.Rect(a.x0, a.y0, a.x1, a.y1)
        for w in words:
            if abs(w.y0 - a.y0) < 3 and a.x0 - 60 < w.x1 <= a.x0 + 1:
                title |= pymupdf.Rect(w.x0, w.y0, w.x1, w.y1)
        text, size = f"{field} -> {value}", 7.4
        width = pymupdf.get_text_length(text, fontname="hebo", fontsize=size) + 6
        cx = min(max((title.x0 + title.x1) / 2, width / 2 + 3), W - width / 2 - 3)
        # Sits in the blank strip just above the title bar, so no printed text is covered.
        box = pymupdf.Rect(cx - width / 2, title.y0 - 13, cx + width / 2, title.y0 - 2.5)
        if box.y0 < 2:
            box = pymupdf.Rect(box.x0, 2, box.x1, 12.5)
        # Don't cover printed text on that strip (the clinic address ends just left of the DOB box).
        covered = [w.x1 for w in words if w.y1 > box.y0 and w.y0 < box.y1 and w.x1 > box.x0 and w.x0 < box.x1]
        if covered:
            shift = min(max(covered) + 3 - box.x0, W - 3 - box.x1)
            box = box + (shift, 0, shift, 0)
        page.draw_rect(box, color=RED, fill=(1, 1, 1), width=0.8)
        page.insert_text((box.x0 + 3, box.y1 - 2.6), text, fontsize=size, fontname="hebo", color=RED)
        placed[field] = [round(c, 1) for c in box]
    return placed


def codeish(text: str) -> bool:
    """An ICD-10 code as OCR reads it off these forms ("GS6.02.-" is G56.02, "H92.O9" is H92.09)."""
    t = text.strip(".,;:-() '")
    if len(t) < 4 or not t[0].isalpha():
        return False
    return bool(re.fullmatch(r"\d{2}\.?\d{1,4}", t[1:].upper().translate(str.maketrans("SOIL", "5011"))))


def outline_row(page, words: list, phrase: list[str], below: float = 0.0) -> list:
    """Red box around a printed diagnosis row, e.g. ["Carpal", "Tunnel"]: the row's printed text plus the
    code cells on either side (the stamp explains the change). The neighbouring column's codes stay outside.
    below: only rows lower than this fraction of the page (the form prints some rows twice)."""
    import pymupdf
    hits = [w for w in words if similar(w.text, phrase[0]) >= 0.8 and w.y0 >= page.rect.height * below]
    for w0 in hits:
        line = sorted((w for w in words if abs(w.cy - w0.cy) < 3.5), key=lambda w: w.x0)
        if not all(any(similar(w.text, p) >= 0.8 for w in line) for p in phrase):
            continue
        start = min(w.x0 for w in line if any(similar(w.text, p) >= 0.8 for p in phrase))
        # left edge: a code right next to the text is this row's own code cell (keep it);
        # further away it belongs to the next column over (stop just after it)
        lefts = [w for w in line if w.x1 <= start and codeish(w.text)]
        lc = max(lefts, key=lambda w: w.x1) if lefts else None
        if lc and start - lc.x1 < 45:
            x0 = lc.x0 - 3
        elif lc:
            x0 = lc.x1 + 3
        else:
            x0 = start - 42
        # right edge: the first code after the text, widened to its column (pen marks garble the OCR box)
        rights = [w for w in line if w.x0 > start and codeish(w.text)]
        rc = min(rights, key=lambda w: w.x0) if rights else None
        if rc:
            column = [w for w in words if abs(w.cy - w0.cy) < 70 and abs(w.x0 - rc.x0) < 8 and codeish(w.text)]
            x1 = max([rc.x1] + [w.x1 for w in column]) + 3
        else:
            x1 = max(w.x1 for w in line if w.x0 >= start) + 42
        inside = [w for w in line if w.x0 >= x0 and w.x1 <= x1] or line
        rect = pymupdf.Rect(x0, min(w.y0 for w in inside) - 2.5, x1, max(w.y1 for w in inside) + 2.5)
        page.draw_rect(rect, color=RED, width=1.3)
        return [round(c, 1) for c in rect]
    raise LayoutError(f"couldn't find the row \"{' '.join(phrase)}\"")


STAMP_SIZES = (7.6, 7.0, 6.4, 5.8, 5.4, 5.0)
STAMP_PAD = (5, 3, -3, -3)      # text inset inside the stamp's red box
TOP_CLEAR = 80                  # the header boxes (patient name, DATE OF BIRTH, DATE OF SERVICE): never stamp here


def stamp_box(page_w: float, page_h: float, x0: float, top: float, text: str):
    """The stamp's box (just tall enough for the text, wrapped lines included) and font size: the largest size
    that fits between `top` and the bottom margin. LayoutError if even the smallest doesn't."""
    import pymupdf
    room = pymupdf.Rect(x0, max(top, TOP_CLEAR), page_w - 8, page_h - 3)
    if room.width > 40 and room.height > 10:
        probe = pymupdf.open().new_page(width=page_w, height=page_h)
        inner = room + STAMP_PAD
        for size in STAMP_SIZES:
            spare = probe.insert_textbox(inner, text, fontsize=size, fontname="helv")   # measures, wrapping included
            if spare >= 0:
                return pymupdf.Rect(room.x0, room.y0, room.x1, min(room.y1, room.y1 - spare + 1)), size
    raise LayoutError("no room for the stamp under the PROVIDER box")


def amend_page(src_pdf: str, page_index: int, out_pdf: str, *, provider_surname: str, roster_surnames: list[str],
               attending_column: str = "", attending_side: str = "", incident_to: str = "", stamp: Stamp,
               words: list | None = None, preview_png: str = "", corrections: dict | None = None,
               circle_provider: bool = True, outline_rows: list[list[str]] | None = None) -> dict:
    """Write a one-page amended PDF. Returns where the marks went (for tests and the log).

    corrections: {"DOB": "01/28/1951", "DOS": "09/24/2026"} written in red in the header boxes.
    circle_provider=False leaves the provider list alone (for a page that only needs a date fixed).
    outline_rows: printed rows to box in red, e.g. [["Carpal", "Tunnel"]] or [(["Carpal", "Tunnel"], 0.3)] to
    only look below 30% of the page; say why in stamp.extra."""
    import pymupdf
    src = pymupdf.open(src_pdf)
    out = pymupdf.open()
    out.insert_pdf(src, from_page=page_index, to_page=page_index)
    src.close()
    page = out[0]
    if page.rotation:
        # Scanned upside down (/Rotate 180): make the copy upright so OCR positions and our
        # drawing coordinates are the same space, and billing sees it the right way up.
        page.remove_rotation()
    if incident_to and not stamp.incident_to:
        stamp.incident_to = incident_to.upper()
    W, H = page.rect.width, page.rect.height
    if words is None:
        words = ocr.words_of(ocr.ocr_page(page))
    try:
        lay = locate(words, W, H, provider_surname, roster_surnames)
    except LayoutError:
        if circle_provider or attending_column or attending_side:
            raise
        # Only a date is being fixed: any row of the list is enough to place the stamp
        # (the provider's own circle often makes their name unreadable to OCR).
        lay = next((lay for s in roster_surnames if (lay := _try_locate(words, W, H, s, roster_surnames))), None)
        if lay is None:
            raise
    row = lay["row"]
    marks = {"provider_row": list(row), "provider_row_inferred": lay["inferred"]}
    if circle_provider:
        page.draw_oval(row + (-9, -3.5, 9, 3.5), color=RED, width=1.4)
    if corrections:
        marks["corrections"] = write_corrections(page, words, corrections)
    if outline_rows:
        marks["outlined"] = [outline_row(page, words, *(row if isinstance(row, tuple) else (row,))) for row in outline_rows]
    if attending_column or attending_side:
        x = column_x(lay, attending_column, attending_side)
        _circled_x(page, x, (row.y0 + row.y1) / 2)
        marks["attending_x"] = x
    if incident_to and lay["medicare"] is not None:
        m = lay["medicare"]
        x = column_x(lay, "", "left" if incident_to.upper() == "YES" else "right")
        page.draw_oval(pymupdf.Rect(x - 11, m.y0 - 2.5, x + 11, m.y1 + 2.5), color=RED, width=1.4)
        marks["incident_to"] = incident_to.upper()

    now = datetime.now()
    text = "\n".join(stamp.lines(now))
    # Stamp goes in the blank space under the PROVIDER box, as wide as the box plus its columns.
    x0 = min(lay["col_x"].get("left", lay["left_edge"] - 40) - 10, lay["left_edge"] - 40)
    bottom = max((w.y1 for w in lay["region"] if w.y0 > lay["first"].y0 and w.x1 > x0 and w.y1 < H - 20), default=row.y1)
    box, size = stamp_box(W, H, x0, bottom + 6, text)
    page.draw_rect(box, color=RED, fill=(1, 1, 1), width=1.2)
    if page.insert_textbox(box + STAMP_PAD, text, fontsize=size, fontname="helv", color=RED) < 0:
        raise LayoutError("stamp text did not fit")
    marks["stamp"] = list(box)

    meta = out.metadata or {}
    meta.update({
        "title": f"Amended superbill {stamp.dos}".strip(),
        "subject": f"Provider: {stamp.provider}" + (f"; Attending: {stamp.attending}" if stamp.attending else ""),
        "keywords": f"{KEYWORD};acct={stamp.account};dos={stamp.dos};by={stamp.initials}",
        "creator": "DizzyDoctor",
        "producer": "DizzyDoctor",
        "creationDate": pymupdf.get_pdf_now(),
        "modDate": pymupdf.get_pdf_now(),
    })
    out.set_metadata(meta)
    os.makedirs(os.path.dirname(out_pdf) or ".", exist_ok=True)
    out.save(out_pdf, garbage=3, deflate=True)
    if preview_png:
        page.get_pixmap(dpi=110).save(preview_png)
    out.close()
    return marks


def amended_keywords(words: str) -> bool:
    """The keywords the app stamps on its own amended copies, under this name or an earlier one."""
    return any(k in (words or "") for k in (KEYWORD, *LEGACY_KEYWORDS))


def is_our_amendment(pdf_path: str) -> bool:
    try:
        import pymupdf
        with pymupdf.open(pdf_path) as d:
            return amended_keywords((d.metadata or {}).get("keywords") or "")
    except Exception:
        return False


# --------------------------------------------------------------------------- filing

def clean(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "", s).strip()


def billing_name(batch_code: str, day: date, last: str, first: str) -> str:
    """The office's name for a superbill sent to billing (office folder and ShareFile):
    "Sample Alice AB 09.24.26 SB.pdf"."""
    return clean(f"{last.title()} {first.title()} {batch_code} {day:%m.%d.%y} SB") + ".pdf"


def unique_path(folder: str, name: str) -> str:
    base, ext = os.path.splitext(name)
    path, n = os.path.join(folder, name), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{base} ({n}){ext}"), n + 1
    return path


def billing_folder(template: str, day: date) -> str:
    """Expand a folder template such as "S:\\CLINIC\\{yyyy}\\{Month}\\Missing" for a date of service."""
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December"]
    return (template.replace("{yyyy}", f"{day.year}").replace("{yy}", f"{day.year % 100:02}")
            .replace("{Month}", months[day.month - 1]).replace("{mm}", f"{day.month:02}"))


def copy_to(src: str, folder: str, name: str) -> str:
    os.makedirs(folder, exist_ok=True)
    dest = unique_path(folder, name)
    shutil.copy2(src, dest)
    return dest
