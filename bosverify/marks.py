"""Finding the hand-applied marks on a page: rubber stamps and signatures.

Body text on these pages is printed in black; the things that make a page
*authorised* - the HOD's and Principal's rubber stamps, the round seal, and
the signatures over them - are applied in blue or violet ink.  That colour
difference is the handle this module pulls on, because OCR frequently cannot
read a stamp at all: the Principal's stamp in particular is often struck
through by a signature and comes back from Tesseract as nothing.

A stamp is a small block of neatly stacked text lines.  A signature is a
loose, sprawling stroke.  The two are told apart by how much of their
bounding box the ink actually fills and by how text-like the rows are.
"""
import cv2
import numpy as np

from .imaging import ink_layers, paper_tint

# Ink must be this much more colourful than the page's own paper to count as
# applied by hand rather than printed.
CHROMA_MARGIN = 18.0
CHROMA_FLOOR = 26.0

MIN_BLOCK_IN = 0.35      # ignore specks smaller than this (inches, longest side)
MAX_BLOCK_FRAC = 0.75    # ignore anything spanning most of the page


def colour_ink_mask(bgr):
    """Pixels that look like applied ink rather than printed body text."""
    chroma, _ = ink_layers(bgr)
    tint = paper_tint(bgr)
    return (chroma > max(CHROMA_FLOOR, tint + CHROMA_MARGIN)).astype(np.uint8)


def _blocks(mask, dpi):
    """Group ink into blocks, merging characters into lines and lines into
    stamps."""
    kx = max(3, int(dpi * 0.10))
    ky = max(3, int(dpi * 0.035))
    joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(joined, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if max(w, h) < MIN_BLOCK_IN * dpi:
            continue
        out.append(dict(x=int(x), y=int(y), w=int(w), h=int(h), area=int(area)))
    return out


def _merge(boxes, dpi):
    """Merge blocks that sit close enough to be one stamp."""
    gap = dpi * 0.16
    boxes = sorted(boxes, key=lambda b: (b["y"], b["x"]))
    merged = []
    for b in boxes:
        for m in merged:
            overlap_x = (min(b["x"] + b["w"], m["x"] + m["w"]) - max(b["x"], m["x"]))
            near_y = (b["y"] - (m["y"] + m["h"]) < gap) and (m["y"] - (b["y"] + b["h"]) < gap)
            if overlap_x > 0.25 * min(b["w"], m["w"]) and near_y:
                x0, y0 = min(m["x"], b["x"]), min(m["y"], b["y"])
                x1 = max(m["x"] + m["w"], b["x"] + b["w"])
                y1 = max(m["y"] + m["h"], b["y"] + b["h"])
                m.update(x=x0, y=y0, w=x1 - x0, h=y1 - y0, area=m["area"] + b["area"])
                break
        else:
            merged.append(dict(b))
    return merged


def _row_profile(sub):
    """How text-like a block is: rows of ink separated by clear gaps."""
    if sub.size == 0:
        return 0.0, 0
    rows = sub.mean(axis=1)
    inked = rows > 0.06
    transitions = int(np.sum(inked[1:] != inked[:-1]))
    return float(inked.mean()), transitions


def find_marks(bgr, dpi=150, exclude=()):
    """Locate stamps and signatures.

    `exclude` is an iterable of (x, y, r) circles - normally the seals - whose
    ink should not be mistaken for a stamp.

    Returns a list of dicts with a box, a `kind` of "stamp" or "signature",
    and the ink statistics behind that call.
    """
    mask = colour_ink_mask(bgr)
    for cx, cy, r in exclude:
        cv2.circle(mask, (int(cx), int(cy)), int(r * 1.12), 0, -1)

    h, w = mask.shape
    marks = []
    for b in _merge(_blocks(mask, dpi), dpi):
        if b["w"] > MAX_BLOCK_FRAC * w and b["h"] > MAX_BLOCK_FRAC * h:
            continue
        sub = mask[b["y"]:b["y"] + b["h"], b["x"]:b["x"] + b["w"]]
        fill = float(sub.mean())
        row_cover, transitions = _row_profile(sub)
        lines = max(1, transitions // 2)
        # A rubber stamp is dense, rectangular and made of stacked lines; a
        # signature is a sparse stroke that wanders across its own box.
        stamp_like = (fill > 0.10 and lines >= 2 and b["w"] > b["h"]
                      and row_cover > 0.35)
        marks.append(dict(x=b["x"], y=b["y"], w=b["w"], h=b["h"],
                          kind="stamp" if stamp_like else "signature",
                          fill=round(fill, 3), lines=lines,
                          row_cover=round(row_cover, 3)))
    marks.sort(key=lambda m: (m["y"], m["x"]))
    return marks


def words_in(page_text, box, scale=1.0):
    """OCR words whose centre falls inside a box (box in image coords)."""
    x0, y0 = box["x"] * scale, box["y"] * scale
    x1, y1 = (box["x"] + box["w"]) * scale, (box["y"] + box["h"]) * scale
    out = []
    for word in page_text.words:
        cx = word["x"] + word["w"] / 2.0
        cy = word["y"] + word["h"] / 2.0
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            out.append(word["text"])
    return out
