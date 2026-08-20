"""Running every detector over a BoS PDF, once, and holding the result.

Rendering and OCR dominate the cost, so each page is rendered twice (once at
working resolution for the image work, once higher for OCR), analysed, and
then released.  Only the derived facts and a small thumbnail are kept, which
keeps a hundred-page document inside a sensible amount of memory.
"""
import base64
import io
import time

import cv2
import numpy as np
import pymupdf

from . import letterhead as letterhead_mod
from . import structure
from .authority import page_authority
from .marks import find_marks
from .ocr import read_page
from .seals import find_seals

WORK_DPI = 150
OCR_DPI = 220
THUMB_WIDTH = 340


class Page:
    """Everything known about one page."""

    def __init__(self, number):
        self.number = number          # 1-based, as a person would cite it
        self.kinds = set()
        self.text = ""
        self.rotation = 0
        self.seals = []
        self.authority = {}
        self.letterhead = {}
        self.chart = {}
        self.page_no = None
        self.thumbnail = None
        self.readable_words = 0

    @property
    def seal_state(self):
        if not self.seals:
            return "missing"
        return ("present" if self.seals[0]["confidence"] == "confirmed"
                else "probable")


class Document:
    def __init__(self, path, name=None):
        self.path = path
        self.name = name or path
        self.pages = []
        self.meeting_date = None
        self.semester = None
        self.elapsed = 0.0
        self.checks = []

    @property
    def full_text(self):
        return "\n".join(p.text for p in self.pages)

    def pages_of(self, kind):
        return [p for p in self.pages if kind in p.kinds]


def _render(doc, index, dpi):
    pix = doc[index].get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n)
    if pix.n == 4:
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    if pix.n == 1:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def _rotate(bgr, degrees):
    if degrees == 90:
        return cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE)
    if degrees == 180:
        return cv2.rotate(bgr, cv2.ROTATE_180)
    if degrees == 270:
        return cv2.rotate(bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return bgr


def _thumbnail(bgr, page):
    """A small annotated preview: seals circled, so a person can check."""
    scale = THUMB_WIDTH / float(bgr.shape[1])
    small = cv2.resize(bgr, (THUMB_WIDTH, int(bgr.shape[0] * scale)))
    for s in page.seals:
        colour = (0, 170, 0) if s["confidence"] == "confirmed" else (0, 165, 255)
        cv2.circle(small, (int(s["x"] * scale), int(s["y"] * scale)),
                   max(3, int(s["r"] * scale)), colour, 2)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def analyse(path, name=None, progress=None):
    """Analyse a BoS PDF and return a populated Document."""
    started = time.time()
    document = Document(path, name)
    ocr_scale = OCR_DPI / float(WORK_DPI)

    with pymupdf.open(path) as pdf:
        total = pdf.page_count
        for index in range(total):
            if progress:
                progress(index, total)
            page = Page(index + 1)
            page_text = read_page(_render(pdf, index, OCR_DPI))
            page.text = page_text.text
            page.rotation = page_text.rotation

            # The OCR reports word positions in whatever orientation it found
            # readable.  Turn the working image the same way, or every
            # position-aware test - stamps, signatures, letterhead - is
            # comparing coordinates from two different frames.
            work = _rotate(_render(pdf, index, WORK_DPI), page_text.rotation)

            page.seals = find_seals(work, dpi=WORK_DPI)
            marks = find_marks(work, dpi=WORK_DPI,
                               exclude=[(s["x"], s["y"], s["r"]) for s in page.seals])
            page.authority = page_authority(work, page_text, page.seals, marks,
                                            dpi=WORK_DPI, ocr_scale=ocr_scale)
            page.letterhead = letterhead_mod.detect(work, page_text, dpi=WORK_DPI,
                                                    ocr_scale=ocr_scale)
            page.kinds = structure.classify(page_text)
            page.page_no = structure.page_numbering(page_text)
            page.chart = structure.has_chart(work, dpi=WORK_DPI)
            page.readable_words = len(page_text.confident_words())
            page.thumbnail = _thumbnail(work, page)
            page._page_text = page_text
            document.pages.append(page)

    document.meeting_date = structure.meeting_date(
        [p._page_text for p in document.pages])
    document.semester = structure.semester_of(document.meeting_date)
    document.elapsed = time.time() - started
    if progress:
        progress(total, total)
    return document
