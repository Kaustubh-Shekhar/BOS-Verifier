"""Who signed and stamped a page.

Several rules in the checklist turn on the same question: does this page carry
the HOD's and the Principal's endorsement - stamp, signature and round seal?
No single cue answers it on every scan:

  * The HOD's stamp usually reads in OCR ("HEAD," / "Department of ..."), but
    not always - on a poor scan it comes back as noise.
  * The Principal's stamp very often does not read at all.  It is normally
    struck through by a signature; across the sample documents the literal
    word "Principal" survives OCR on only two of the five pages carrying it.
  * On some scans stamp ink is violet and separates cleanly from black body
    text by colour; on others the whole page is colour-cast and the stamp ink
    is nearly black, so colour separates nothing.

So a stamp is located as a *cluster of the college address block* low on the
page - every stamp repeats the college name and PIN code - drawing on OCR
word positions and on colour blocks, whichever the scan supports.  Roles are
then read off each cluster.  The evidence behind each answer is reported, so
a page resting on weaker cues can be flagged for a human rather than quietly
passed.
"""
import re

import cv2
import numpy as np

from .imaging import ink_layers

# Words that appear in the address block of any of the college's stamps.
ADDRESS_CUE = re.compile(r"xavier|autono|ahmedab|380|009|colle", re.I)
PRINCIPAL_RE = re.compile(r"pr[il1]n[cev][il1]p|principal", re.I)
HOD_RE = re.compile(r"\bhead\b|\bhod\b|department\s+of|dept\.?\s+of", re.I)

# Stamps sit below the body of the page.  The cut-off has to stay generous:
# on a short page the endorsement block lands not much past a third of the
# way down.  It exists only to keep the printed heading, which repeats the
# college name, from being read as a stamp.
STAMP_ZONE_TOP = 0.30
CLUSTER_GAP_IN = 0.55
# The letterhead's own footer carries the college address as well, so a
# cluster that reads like the printed footer is not a stamp.
FOOTER_CUE = re.compile(
    r"navrangpura|gujarat|india|website|e-?mail|jesuits|sxca|p\.?\s?b\.?\s?no|"
    r"\d{5,}|www\.|@|camscanner|re-?accredited|naac|affiliated", re.I)


def _overlaps(a, b, frac=0.25):
    """Do two boxes share a meaningful part of the smaller one?"""
    ix = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    iy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    if ix <= 0 or iy <= 0:
        return False
    smaller = min(a["w"] * a["h"], b["w"] * b["h"])
    return smaller > 0 and (ix * iy) / smaller >= frac


def _cluster(points, gap):
    """Single-link clustering of (x, y, payload) by distance."""
    clusters = []
    for pt in sorted(points, key=lambda p: (p[1], p[0])):
        for c in clusters:
            if any(abs(pt[0] - q[0]) < gap * 2.2 and abs(pt[1] - q[1]) < gap
                   for q in c):
                c.append(pt)
                break
        else:
            clusters.append([pt])
    # A second pass merges clusters that grew towards each other.
    merged = True
    while merged:
        merged = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                if any(abs(a[0] - b[0]) < gap * 2.2 and abs(a[1] - b[1]) < gap
                       for a in clusters[i] for b in clusters[j]):
                    clusters[i].extend(clusters[j])
                    del clusters[j]
                    merged = True
                    break
            if merged:
                break
    return clusters


def stamp_blocks(page_text, marks, shape, dpi=150, ocr_scale=1.0):
    """Locate rubber-stamp impressions in the signature area of a page.

    Uses OCR word positions where the stamp text survived, and colour blocks
    where it did not.  Returns dicts with a box, the words inside it, and the
    role read off those words.
    """
    height, width = shape[:2]
    top = STAMP_ZONE_TOP * height
    gap = CLUSTER_GAP_IN * dpi

    points = []
    for word in page_text.words:
        if word["conf"] < 30 or not ADDRESS_CUE.search(word["text"]):
            continue
        cx = (word["x"] + word["w"] / 2.0) / ocr_scale
        cy = (word["y"] + word["h"] / 2.0) / ocr_scale
        if cy < top:
            continue
        points.append((cx, cy, word["text"]))

    blocks = []
    for cluster in _cluster(points, gap):
        xs = [p[0] for p in cluster]
        ys = [p[1] for p in cluster]
        blocks.append(dict(x=int(min(xs) - 0.3 * dpi), y=int(min(ys) - 0.3 * dpi),
                           w=int(max(xs) - min(xs) + 0.6 * dpi),
                           h=int(max(ys) - min(ys) + 0.6 * dpi),
                           source="text"))

    # Colour blocks catch stamps whose text did not survive OCR at all.
    for m in marks:
        if m["kind"] != "stamp" or m["y"] + m["h"] / 2 < top:
            continue
        if any(_overlaps(m, b) for b in blocks):
            continue
        blocks.append(dict(x=m["x"], y=m["y"], w=m["w"], h=m["h"], source="ink"))

    # A stamp names its office on the line *above* the address - "HEAD," or
    # "Principal" - so read a band above each cluster as well, or the role is
    # never recovered.
    lift = 0.6 * dpi
    for b in blocks:
        inside = []
        for word in page_text.words:
            cx = (word["x"] + word["w"] / 2.0) / ocr_scale
            cy = (word["y"] + word["h"] / 2.0) / ocr_scale
            if (b["x"] - 0.25 * dpi <= cx <= b["x"] + b["w"] + 0.25 * dpi
                    and b["y"] - lift <= cy <= b["y"] + b["h"] + 0.2 * dpi):
                inside.append(word["text"])
        joined = " ".join(inside)
        b["words"] = joined
        if PRINCIPAL_RE.search(joined):
            b["role"] = "principal"
        elif HOD_RE.search(joined):
            b["role"] = "hod"
        else:
            b["role"] = "unlabelled"
    # The letterhead's printed footer clusters just like a stamp does, and is
    # only recognisable once the whole box has been read - the address words
    # alone look identical.  A stamp is several words; a single stray match is
    # not, and the seed word alone is a poor size test because a badly scanned
    # stamp yields only one word above the confidence floor.
    blocks = [b for b in blocks
              if not FOOTER_CUE.search(b["words"])
              and (b["source"] == "ink" or len(b["words"].split()) >= 3)]
    blocks.sort(key=lambda b: (b["y"], b["x"]))
    return blocks


def _roles(page_text, blocks, zone_text):
    """Decide which endorsements a page carries, and how well established."""
    labelled = {b["role"] for b in blocks}

    unlabelled = [b for b in blocks if b["role"] == "unlabelled"]

    if "hod" in labelled:
        hod = dict(found=True, strength="strong", how="a stamp reading 'HEAD / Department of'")
    elif "principal" in labelled and unlabelled:
        # Two stamps, one of them clearly the Principal's: the other is the
        # HOD's, whose office line did not survive the scan.
        hod = dict(found=True, strength="medium",
                   how="a second stamp alongside the Principal's, its text not legible")
    elif HOD_RE.search(zone_text):
        hod = dict(found=True, strength="medium",
                   how="'HEAD'/'Department of' low on the page")
    else:
        hod = dict(found=False, strength=None, how=None)

    if "principal" in labelled:
        principal = dict(found=True, strength="strong",
                         how="a stamp reading 'Principal'")
    elif PRINCIPAL_RE.search(zone_text):
        principal = dict(found=True, strength="medium",
                         how="the word 'Principal' in the signature area")
    elif hod["found"] and len(blocks) >= 2:
        principal = dict(found=True, strength="medium",
                         how="a second stamp alongside the HOD's, its text not legible")
    else:
        principal = dict(found=False, strength=None, how=None)
    return hod, principal


# The loose text fallback only looks at the foot of the page.  Searching the
# whole signature area matched "Department of ..." in a page heading and
# reported an endorsement that was not there.
FALLBACK_ZONE_TOP = 0.62


def _zone_text(page_text, shape, ocr_scale):
    """Words at the foot of the page, excluding the printed letterhead footer."""
    top = FALLBACK_ZONE_TOP * shape[0]
    kept = []
    for word in page_text.words:
        cy = (word["y"] + word["h"] / 2.0) / ocr_scale
        if cy >= top and not FOOTER_CUE.search(word["text"]):
            kept.append(word["text"])
    return " ".join(kept)


def find_signatures(bgr, page_text, dpi=150, ocr_scale=1.0, exclude=()):
    """Ink the OCR could not read - which is what a signature looks like.

    Printed text and rubber-stamp text are recognised by Tesseract and masked
    out; what is left, if it is big enough and sprawls rather than sits in a
    block, is handwriting.
    """
    chroma, dark = ink_layers(bgr)
    ink = ((dark > 40) | (chroma > 60)).astype(np.uint8)

    h, w = ink.shape
    pad = max(2, int(0.02 * dpi))
    for word in page_text.confident_words(min_conf=55):
        x = int(word["x"] / ocr_scale)
        y = int(word["y"] / ocr_scale)
        ww = int(word["w"] / ocr_scale)
        hh = int(word["h"] / ocr_scale)
        cv2.rectangle(ink, (x - pad, y - pad), (x + ww + pad, y + hh + pad), 0, -1)
    for cx, cy, r in exclude:
        cv2.circle(ink, (int(cx), int(cy)), int(r * 1.12), 0, -1)
    # Ignore the margins, where scanner shadow leaves long dark smears.
    margin = int(0.045 * min(h, w))
    ink[:margin, :] = 0
    ink[-margin:, :] = 0
    ink[:, :margin] = 0
    ink[:, -margin:] = 0

    joined = cv2.morphologyEx(
        ink, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT,
                                  (max(3, int(dpi * 0.05)), max(3, int(dpi * 0.02)))))
    n, _, stats, _ = cv2.connectedComponentsWithStats(joined, 8)
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        long_side = max(bw, bh)
        if long_side < 0.20 * dpi or long_side > 3.0 * dpi:
            continue
        if area < 0.006 * dpi * dpi:
            continue
        fill = area / float(bw * bh)
        # A signature is a stroke: it wanders across its box without filling it,
        # unlike a solid blot or a block of print the OCR happened to miss.
        if not (0.05 < fill < 0.62):
            continue
        # The rule a signature is written on top of survives the same way a
        # signature does - the OCR cannot read it either - but it is a hairline
        # running the width of the column, not handwriting.
        if min(bw, bh) < 0.07 * dpi or max(bw, bh) > 12 * max(1, min(bw, bh)):
            continue
        out.append(dict(x=int(x), y=int(y), w=int(bw), h=int(bh),
                        area=int(area), fill=round(float(fill), 3)))
    out.sort(key=lambda s: -s["area"])
    return out


def page_authority(bgr, page_text, seals, marks, dpi=150, ocr_scale=1.0):
    """Summarise the endorsements on one page."""
    blocks = stamp_blocks(page_text, marks, bgr.shape, dpi=dpi, ocr_scale=ocr_scale)
    hod, principal = _roles(page_text, blocks, _zone_text(page_text, bgr.shape, ocr_scale))
    signatures = find_signatures(bgr, page_text, dpi=dpi, ocr_scale=ocr_scale,
                                 exclude=[(s["x"], s["y"], s["r"]) for s in seals])
    return dict(
        hod=hod,
        principal=principal,
        stamps=blocks,
        signatures=len(signatures),
        signature_boxes=signatures,
        seal=bool(seals),
        seal_confirmed=any(s["confidence"] == "confirmed" for s in seals),
        seals=seals,
    )
