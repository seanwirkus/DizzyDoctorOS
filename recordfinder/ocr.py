"""Read scanned PDFs with the OCR engine built into the operating system.

Nothing leaves the PC: pages are rendered locally with PyMuPDF and recognized by
Windows.Media.Ocr on Windows 10/11, or by Apple's Vision framework on a Mac. Results are cached
in the local index database (index.Store), keyed by path + size + modified time, so each scanned
batch is only read once.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from dataclasses import dataclass

DPI = 200  # a letter page becomes 1700x2200 px, well under the engine's 10,000 px limit

try:  # optional at import time so the rest of the app (and the tests) run anywhere
    import pymupdf
except ImportError:  # pragma: no cover
    pymupdf = None
try:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
except ImportError:  # pragma: no cover - not Windows, or the winrt packages are missing
    OcrEngine = None
try:
    import Vision
    from Foundation import NSData, NSMakeRange
except ImportError:  # pragma: no cover - not a Mac, or pyobjc-framework-Vision is missing
    Vision = None


@dataclass
class Word:
    text: str
    x0: float  # PDF points, origin top-left
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


Lines = list[list[Word]]  # one page: lines of words, top to bottom


def unavailable_reason() -> str:
    """Empty when OCR can run on this PC, otherwise why not."""
    if pymupdf is None:
        return "PyMuPDF is not installed"
    if OcrEngine is None and Vision is None:
        return "No OCR engine on this computer (Windows OCR, or Vision on a Mac)"
    return ""


_engine = None
_engine_lock = threading.Lock()


def _get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = OcrEngine.try_create_from_language(Language("en-US")) or OcrEngine.try_create_from_user_profile_languages()
            if _engine is None:
                raise RuntimeError("No OCR language is installed (Windows Settings > Time & language > Language)")
    return _engine


async def _recognize(pix) -> list:
    if not pix.alpha:
        pix = pymupdf.Pixmap(pix, 1)  # add alpha: RGB -> RGBA
    buf = bytearray(pix.samples)
    buf[0::4], buf[2::4] = buf[2::4], buf[0::4]  # RGBA -> BGRA, which is what the engine takes
    writer = DataWriter()
    writer.write_bytes(bytes(buf))
    bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, pix.width, pix.height)
    result = await _get_engine().recognize_async(bmp)
    return [[(w.text, w.bounding_rect.x, w.bounding_rect.y, w.bounding_rect.width, w.bounding_rect.height)
             for w in line.words] for line in result.lines]


def _recognize_vision(pix) -> list:
    """Apple Vision: lines of (text, x, y, w, h) in pixels, top-left origin, like the Windows engine gives."""
    png = pix.tobytes("png")
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)   # codes and dates, not prose
    req.setRecognitionLanguages_(["en-US"])  # else handwriting comes back with accents and Cyrillic letters
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(NSData.dataWithBytes_length_(png, len(png)), None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision OCR failed: {err}")
    W, H = pix.width, pix.height
    lines = []
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        text = cand[0].string()
        words, pos = [], 0
        for tok in text.split():
            start = text.index(tok, pos)
            pos = start + len(tok)
            box, _err = cand[0].boundingBoxForRange_error_(NSMakeRange(start, len(tok)), None)
            bb = box.boundingBox() if box is not None else obs.boundingBox()
            words.append((tok, bb.origin.x * W, (1 - bb.origin.y - bb.size.height) * H, bb.size.width * W, bb.size.height * H))
        if words:
            lines.append(words)
    lines.sort(key=lambda ln: (ln[0][2], ln[0][1]))
    return lines


def ocr_page(page, dpi: int = DPI, clip=None) -> Lines:
    """OCR one PyMuPDF page (or a clip of it). Word boxes come back in PDF points."""
    if unavailable_reason():
        raise RuntimeError(unavailable_reason())
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    k = 72 / dpi
    ox, oy = (clip.x0, clip.y0) if clip is not None else (0.0, 0.0)
    raw = asyncio.run(_recognize(pix)) if OcrEngine is not None else _recognize_vision(pix)
    return [[Word(t, ox + x * k, oy + y * k, ox + (x + w) * k, oy + (y + h) * k) for t, x, y, w, h in line]
            for line in raw if line]


def text_of(lines: Lines) -> str:
    return "\n".join(" ".join(w.text for w in line) for line in lines)


def words_of(lines: Lines) -> list[Word]:
    return [w for line in lines for w in line]


def dumps(lines: Lines) -> str:
    return json.dumps([[[w.text, round(w.x0, 1), round(w.y0, 1), round(w.x1, 1), round(w.y1, 1)] for w in line]
                       for line in lines], separators=(",", ":"))


def loads(s: str) -> Lines:
    return [[Word(*w) for w in line] for line in json.loads(s)]


def ocr_pdf(path: str, store=None, progress=None, cancel: threading.Event | None = None) -> list[Lines]:
    """OCR every page of a PDF, reusing cached pages from the store when the file hasn't changed."""
    st = os.stat(path)
    cached = store.get_ocr(path, st.st_size, st.st_mtime) if store is not None else {}
    doc = pymupdf.open(path)
    pages: list[Lines] = []
    try:
        for i, page in enumerate(doc):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            if i in cached:
                pages.append(cached[i])
                continue
            if progress:
                progress(i + 1, len(doc))
            lines = ocr_page(page)
            if store is not None:
                store.put_ocr(path, st.st_size, st.st_mtime, i, lines)
            pages.append(lines)
    finally:
        doc.close()
    return pages
