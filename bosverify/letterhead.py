"""Deciding whether a page is printed on the college letterhead.

Two rules need this: the first page of a BoS must be on letterhead, and so
must the attendance sheet.  The distinction that matters is between the
*printed* letterhead - crest, college name set large, a coloured rule, and
the address footer - and a page where somebody has merely typed the college's
name at the top.  Both read the same in OCR, so the text alone cannot settle
it; the printed furniture has to be seen.
"""
import re

import cv2
import numpy as np

from .imaging import ink_layers, paper_tint

HEADER_BAND = 0.22
FOOTER_BAND = 0.86
LOGO_ZONE_W = 0.32
LOGO_ZONE_H = 0.20

NAME_RE = re.compile(r"xavier", re.I)
# Deliberately excludes a bare "(Autonomous)": that appears in the typed
# heading of ordinary annexure pages too.  Only the letterhead carries the
# accreditation tagline.
ACCRED_RE = re.compile(r"re-?accredited|naac|affiliated\s+to|cgpa", re.I)
FOOTER_RE = re.compile(
    r"navrangpura|jesuits|sxca\.edu|www\.|e-?mail|website|gujarat,\s*india|"
    r"p\.?\s?b\.?\s?no", re.I)


def _band_text(page_text, shape, ocr_scale, lo, hi):
    height = shape[0]
    y0, y1 = lo * height, hi * height
    out = []
    for word in page_text.words:
        cy = (word["y"] + word["h"] / 2.0) / ocr_scale
        if y0 <= cy <= y1:
            out.append(word["text"])
    return " ".join(out)


def _colour_mask(bgr):
    chroma, _ = ink_layers(bgr)
    tint = paper_tint(bgr)
    return (chroma > max(30.0, tint + 22.0)).astype(np.uint8)


def _has_logo(bgr, dpi):
    """A printed crest: a solid patch of colour in the top-left corner."""
    h, w = bgr.shape[:2]
    zone = _colour_mask(bgr)[:int(LOGO_ZONE_H * h), :int(LOGO_ZONE_W * w)]
    if zone.size == 0:
        return False, 0
    closed = cv2.morphologyEx(zone, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
    best = 0
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw < 0.20 * dpi or bh < 0.20 * dpi:
            continue
        best = max(best, int(area))
    return best > (0.16 * dpi) ** 2, best


def _has_rule(bgr, dpi):
    """A printed coloured rule running across the head of the page."""
    h, w = bgr.shape[:2]
    band = _colour_mask(bgr)[:int(HEADER_BAND * h), :]
    if band.size == 0:
        return False, 0.0
    runs = band.sum(axis=1) / float(w)
    return bool((runs > 0.45).any()), float(runs.max())


def detect(bgr, page_text, dpi=150, ocr_scale=1.0):
    """Report whether the page carries the printed letterhead."""
    header = _band_text(page_text, bgr.shape, ocr_scale, 0.0, HEADER_BAND)
    footer = _band_text(page_text, bgr.shape, ocr_scale, FOOTER_BAND, 1.0)

    name = bool(NAME_RE.search(header))
    accred = bool(ACCRED_RE.search(header))
    logo, logo_area = _has_logo(bgr, dpi)
    rule, rule_width = _has_rule(bgr, dpi)
    foot = bool(FOOTER_RE.search(footer))

    # A letterhead needs both halves: the printed furniture (crest, coloured
    # rule or address footer) *and* the masthead wording that only the
    # letterhead carries.  On a colour-cast scan the crest test alone fires on
    # ordinary pages, and the college's name alone appears on any typed
    # heading, so neither is trusted by itself.
    printed = logo or rule or foot
    wording = accred or foot
    found = bool((name or accred) and printed and wording)

    reasons = []
    if name:
        reasons.append("college name in the masthead")
    if accred:
        reasons.append("accreditation line")
    if logo:
        reasons.append("printed crest")
    if rule:
        reasons.append("coloured rule across the head")
    if foot:
        reasons.append("printed address footer")

    return dict(found=found, reasons=reasons, name=name, accreditation=accred,
                logo=logo, rule=rule, footer=foot,
                logo_area=logo_area, rule_width=round(rule_width, 3))
