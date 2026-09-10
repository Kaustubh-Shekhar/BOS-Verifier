"""Cross-checking who was absent against who signed.

The front of a BoS lists the members and marks the ones who did not come:
"Dr. Ajay Chauhan (Absent)".  The attendance sheet further back has a
signature beside each name.  A signature next to somebody recorded as absent
is a contradiction, and it is exactly the sort of thing that is easy to miss
by eye and awkward to explain afterwards.

Deciding it needs both halves of the document: the names marked absent, read
from the text, and whether there is handwriting in that person's row of the
attendance sheet - which is an image question, since a signature is by
definition ink the OCR could not read.

Two things make the image side harder than it sounds.  The ruled line each
member signs on survives exactly the way a signature does, so the lines are
stripped out first.  And a signature with a tall flourish reaches up into the
row above it: on the sample sheet the alumnus's signature climbs across the
empty line belonging to the absent industry representative.  So a mark whose
weight sits low in the row is reported as needing a look rather than as a
signature, because that is what it honestly is.
"""
import re

import cv2
import numpy as np

from .imaging import ink_layers

# "Dr. Ajay Chauhan (Absent)", "Ms. Prachi K. Bhavsar (Absent)".  Anchoring on
# the honorific keeps the name from running backwards into the previous
# member's: the list reads "Dr. B. D. Dhila Dr. Jigar Parikh (Absent)" as one
# unbroken run of text.
TITLED_RE = re.compile(
    r"((?:Dr|Mr|Ms|Mrs|Prof|Shri|Smt)\.?\s+[A-Z][A-Za-z.'\-]*"
    r"(?:\s+[A-Z][A-Za-z.'\-]*){0,2})\s*[\(\[]\s*absent\s*[\)\]]", re.I)
PLAIN_RE = re.compile(
    r"([A-Z][A-Za-z.'\-]*(?:\s+[A-Z][A-Za-z.'\-]*){1,2})\s*[\(\[]\s*absent\s*[\)\]]",
    re.I)

NAME_MIN = 3
DEFAULT_ROW = 58.0          # row height in working pixels, if it can't be measured
CELL_ABOVE = 0.30           # how far the signing space reaches above the name
CELL_BELOW = 0.70           # and below it, as fractions of a row

# An empty ruled line leaves almost nothing behind once the rule is removed;
# a signature leaves several per cent of the cell inked.  Measured on the
# sample sheets: signed rows 0.05-0.13, empty rows 0.002-0.031.
INK_FLOOR = 0.045
# Ink whose weight sits this far below the middle of the cell is the top of
# the next row's signature reaching up, not this row's own.
CENTROID_MAX = 0.12


def absent_members(page):
    """Names marked "(Absent)" on one page."""
    out = []
    spans = {m.start(): m for m in PLAIN_RE.finditer(page.text)}
    spans.update({m.start(): m for m in TITLED_RE.finditer(page.text)})
    for _, m in sorted(spans.items()):
        name = " ".join(m.group(1).split())
        parts = [w.strip(".,;:") for w in name.split() if len(w.strip(".,;:")) >= NAME_MIN]
        if not parts:
            continue
        surname = parts[-1]
        # Both patterns can match the same person at different offsets; keep
        # the fuller reading of the name.
        existing = next((o for o in out if o["surname"] == surname), None)
        if existing:
            if len(name) > len(existing["name"]):
                existing["name"] = name
            continue
        out.append(dict(name=name, surname=surname, listed_on=page.number,
                        sheets=[]))
    return out


def _unread_ink(bgr, page_text, ocr_scale, dpi):
    """Ink the OCR could not read, with the ruled lines taken out."""
    chroma, dark = ink_layers(bgr)
    ink = ((dark > 40) | (chroma > 60)).astype(np.uint8)
    pad = max(2, int(0.02 * dpi))
    for word in page_text.confident_words(min_conf=55):
        x = int(word["x"] / ocr_scale)
        y = int(word["y"] / ocr_scale)
        w = int(word["w"] / ocr_scale)
        h = int(word["h"] / ocr_scale)
        cv2.rectangle(ink, (x - pad, y - pad), (x + w + pad, y + h + pad), 0, -1)

    # The line a member signs on runs the width of the column; opening with a
    # long flat kernel keeps only such runs, which are then removed.
    rules = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, int(0.4 * dpi)), 1)))
    grown = cv2.dilate(rules, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    return cv2.subtract(ink, grown), rules


def _name_rows(page_text, surname, ocr_scale):
    target = surname.lower().strip(".,;:")
    rows = []
    for word in page_text.words:
        token = word["text"].lower().strip(".,;:()")
        if word["conf"] < 30 or len(token) < NAME_MIN:
            continue
        # Allow for OCR clipping a letter or two, but not for a different
        # person: "Joshi" must not match "Joshipura".
        near = (len(target) >= 5 and token.startswith(target[:5])
                and abs(len(token) - len(target)) <= 2)
        if token != target and not near:
            continue
        rows.append(dict(
            x_end=(word["x"] + word["w"]) / ocr_scale,
            y=(word["y"] + word["h"] / 2.0) / ocr_scale,
        ))
    return rows


def _sheet_geometry(rules, dpi):
    """Row height and where the signature column starts, from the ruled lines."""
    n, _, stats, _ = cv2.connectedComponentsWithStats(rules, 8)
    lines = [(stats[i][0], stats[i][1], stats[i][2]) for i in range(1, n)
             if stats[i][2] > 0.9 * dpi]
    if len(lines) < 3:
        return DEFAULT_ROW, None
    ys = sorted(y for _, y, _ in lines)
    gaps = [b - a for a, b in zip(ys, ys[1:]) if 0.15 * dpi < b - a < 1.0 * dpi]
    row = float(np.median(gaps)) if gaps else DEFAULT_ROW
    left = float(np.median([x for x, _, _ in lines]))
    return row, left


def scan_sheet(bgr, page, page_text, absentees, ocr_scale, dpi=150):
    """Look for handwriting in each absentee's row of this attendance sheet."""
    if not absentees:
        return []
    ink, rules = _unread_ink(bgr, page_text, ocr_scale, dpi)
    row_height, column_left = _sheet_geometry(rules, dpi)
    height, width = ink.shape
    margin = int(0.02 * width)

    findings = []
    for person in absentees:
        rows = _name_rows(page_text, person["surname"], ocr_scale)
        if not rows:
            continue
        if page.number not in person["sheets"]:
            person["sheets"].append(page.number)
        for row in rows:
            left = int(max(column_left or 0, row["x_end"] + 0.15 * dpi))
            top = int(row["y"] - CELL_ABOVE * row_height)
            bottom = int(row["y"] + CELL_BELOW * row_height)
            cell = ink[max(0, top):min(height, bottom), left:max(left + 1, width - margin)]
            if cell.size == 0:
                continue
            frac = float(cell.mean())
            if frac < INK_FLOOR:
                continue
            weight = cell.sum(axis=1)
            centre = float((weight * np.arange(cell.shape[0])).sum() / max(1, weight.sum()))
            offset = centre / cell.shape[0] - 0.5
            findings.append(dict(
                name=person["name"], page=page.number,
                listed_on=person["listed_on"],
                ink=round(frac, 4), offset=round(offset, 2),
                certain=offset <= CENTROID_MAX,
                box=dict(x=left, y=max(0, top),
                         w=max(1, width - margin - left),
                         h=max(1, min(height, bottom) - max(0, top))),
            ))
            break
    return findings
