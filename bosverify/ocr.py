"""OCR for scanned BoS pages.

Everything in a BoS is a photograph of paper, so every textual rule in the
checklist runs on OCR output.  Two wrinkles matter:

  * Some pages (feedback charts, for instance) are bound sideways.  Tesseract's
    own orientation detector is unreliable on them - it reports confidences
    near zero and guesses the wrong script - so orientation is chosen by
    running the OCR and keeping whichever quarter-turn reads best.
  * That trial is only worth paying for when the upright pass looks poor, so
    the common case stays single-pass.
"""
import os
import re

import cv2
import numpy as np
import pytesseract
from PIL import Image

OCR_DPI = 220
# A page that yields at least this many confident words upright is not
# re-tried at other orientations.
GOOD_ENOUGH_WORDS = 25
MIN_WORD_CONF = 60


def _locate_tesseract():
    """Point pytesseract at the binary, honouring an explicit override."""
    override = os.environ.get("TESSERACT_CMD")
    candidates = [override] if override else []
    candidates += [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            return path
    return None  # fall back to whatever is on PATH


_locate_tesseract()


def tesseract_available():
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


class PageText:
    """OCR result for one page, in the page's own upright orientation."""

    def __init__(self, text, words, rotation, size):
        self.text = text
        self.words = words          # list of dicts: text, conf, x, y, w, h
        self.rotation = rotation    # degrees the page was turned to read it
        self.size = size            # (width, height) after rotation
        self.lower = text.lower()
        self.flat = _flatten(text)

    def band(self, lo, hi):
        """Text of the words lying in a horizontal band of the page.

        Headings are what identify a page ("Annexure-2", "Attendance sheet");
        the same words further down are usually just a passing mention in the
        minutes, which is not the same thing at all.
        """
        height = self.size[1] or 1
        y0, y1 = lo * height, hi * height
        return " ".join(w["text"] for w in self.words
                        if y0 <= w["y"] + w["h"] / 2.0 <= y1)

    def has(self, *phrases):
        """True if any phrase appears, ignoring case and OCR spacing noise."""
        return any(_flatten(p) in self.flat for p in phrases)

    def find(self, phrase):
        return _flatten(phrase) in self.flat

    def confident_words(self, min_conf=MIN_WORD_CONF):
        return [w for w in self.words if w["conf"] >= min_conf]


def _flatten(s):
    """Lowercase and strip everything but letters and digits.

    OCR of a scan sprinkles stray punctuation and inconsistent spacing through
    headings, so 'Annexure - 1' , 'Annexure-1' and 'Annexure 1' must all match
    the same probe.
    """
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _rotate(bgr, degrees):
    if degrees == 90:
        return cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE)
    if degrees == 180:
        return cv2.rotate(bgr, cv2.ROTATE_180)
    if degrees == 270:
        return cv2.rotate(bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return bgr


def _run(bgr):
    """OCR one image, returning (text, words, quality)."""
    pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    data = pytesseract.image_to_data(pil, output_type=pytesseract.Output.DICT)
    words, parts = [], []
    for i, raw in enumerate(data["text"]):
        token = raw.strip()
        if not token:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        words.append(dict(text=token, conf=conf, x=int(data["left"][i]),
                          y=int(data["top"][i]), w=int(data["width"][i]),
                          h=int(data["height"][i])))
        parts.append(token)
    # Quality is counted in *words that look like words*: a sideways page
    # still returns plenty of high-confidence single-character garbage.
    quality = sum(1 for w in words
                  if w["conf"] >= MIN_WORD_CONF and len(w["text"]) >= 3
                  and re.search(r"[A-Za-z]{3}", w["text"]))
    return " ".join(parts), words, quality


def read_page(bgr, allow_rotation=True):
    """OCR a page, turning it upright first if that reads better."""
    text, words, quality = _run(bgr)
    best = (quality, text, words, 0, bgr.shape[1::-1])
    if allow_rotation and quality < GOOD_ENOUGH_WORDS:
        for degrees in (90, 270, 180):
            turned = _rotate(bgr, degrees)
            t, w, q = _run(turned)
            if q > best[0]:
                best = (q, t, w, degrees, turned.shape[1::-1])
    _, text, words, rotation, size = best
    return PageText(text, words, rotation, size)


def read_document(images, allow_rotation=True):
    """OCR every page of a document. `images` is an iterable of BGR arrays."""
    return [read_page(img, allow_rotation=allow_rotation) for img in images]
