"""Detection of the college's round rubber seal on a scanned page.

The seal is a stamp roughly 1-1.5in across: an outer rim, a band of curved
text (the college name and city), an inner rim, and the crest in the middle.
Scans render it anywhere from saturated violet to a barely-there pink ghost,
so the detector keys on structure rather than colour:

  * rim alignment - at a stamped rim the image gradient points along the
    radius at every angle.  This is what separates a real circle from a
    signature, a run of text or a patch of scanner shading that merely
    happens to sit in a ring, and it is the test that carries the check;
  * a contiguous arc - the aligned angles must form one continuous sweep,
    not a scatter of lucky hits around the circumference;
  * ring coverage - ink all the way round two concentric rims, which rejects
    the outline of a pie chart (no inner rim, hollow middle);
  * whitespace - a stamp is pressed into clear space, so ink must not carry
    on past the rim.

Seals stamped half over a dark chart block do occur, so the rim tests ignore
angles that fall on dark ground - subject to enough of the rim still being
visible to judge.  Such a stamp is reported at lower confidence rather than
silently passed or failed.
"""
import cv2
import numpy as np

from .imaging import ink_layers, paper_tint

N_ANGLES = 180
OUTER_BAND = (0.92, 1.07)
INNER_BAND = (0.60, 0.85)
OUTSIDE_BAND = (1.20, 1.34)
RIM_BAND = (0.88, 1.12)

GRAD_MIN = 12.0
ALIGN_COS = 0.85         # gradient within ~32 degrees of the radius
PAPER_LEVEL = 120        # below this the rim sample sits on dark ground

# Two acceptance tiers. A stamp that clears CONFIRMED is reported as found;
# one that only clears REVIEW is reported as probable and flagged for a human
# to glance at, which is the honest answer for a half-occluded impression.
CONFIRMED = dict(outside=0.40, visible=0.45, align=0.70, arc=0.38,
                 outer=0.55, inner=0.50)
REVIEW = dict(outside=0.58, visible=0.45, align=0.50, arc=0.30,
              outer=0.40, inner=0.45)

# The crest printed in the letterhead is round-ish and colourful, and always
# sits in the top-left corner, so that corner is not eligible.
LOGO_ZONE = (0.13, 0.42)
# Scanner shadow along the edge of a sheet throws round-ish blobs; a stamp
# that is part of the record is never pressed into the very margin.
BORDER_MARGIN = 0.055
MAX_REFINE = 40
CAND_SCALE = 0.5

_ANG = np.linspace(0, 2 * np.pi, N_ANGLES, endpoint=False)
_COS, _SIN = np.cos(_ANG), np.sin(_ANG)


class PageAnalysis:
    """Per-page maps, computed once and reused by every candidate."""

    def __init__(self, bgr):
        self.shape = bgr.shape[:2]
        chroma, dark = ink_layers(bgr)
        self.chroma = chroma
        tint = paper_tint(bgr)
        # Three sensitivities: a crisp violet stamp shows up in "strong",
        # one that has faded to a pink ghost only appears in "ghost".
        self.fields = [
            ("strong", (chroma > max(20.0, tint + 14.0)) | (dark > 34)),
            ("faint", (chroma > max(6.0, tint + 4.0)) | (dark > 16)),
            ("ghost", (chroma > max(4.0, tint + 2.0)) | (dark > 11)),
        ]
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        self.ground = cv2.medianBlur(gray, 31)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0).astype(np.float32)
        self.gx = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
        self.gy = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)
        self.mag = np.hypot(self.gx, self.gy)


def _sample(shape, cx, cy, radii):
    h, w = shape
    xs = np.rint(cx + radii[:, None] * _COS[None, :]).astype(np.int32)
    ys = np.rint(cy + radii[:, None] * _SIN[None, :]).astype(np.int32)
    ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    return np.clip(xs, 0, w - 1), np.clip(ys, 0, h - 1), ok


def _longest_run(mask):
    """Longest circular run of True, as a fraction of the array length."""
    n = len(mask)
    if mask.all():
        return 1.0
    if not mask.any():
        return 0.0
    best = run = 0
    for value in np.concatenate([mask, mask]):
        run = run + 1 if value else 0
        best = max(best, run)
    return min(best, n) / n


def ring_coverage(ink, cx, cy, r, visible=None):
    """How much of each concentric ring carries ink.

    `visible` is a per-angle mask of the rim that sits on light ground.  The
    whitespace test outside the rim is judged only over those angles: where
    the stamp overlaps a dark chart block there is no whitespace to find, and
    counting that as ink would condemn a perfectly good impression.
    """
    rhos = np.arange(0.55, 1.36, 0.02)
    xs, ys, ok = _sample(ink.shape, cx, cy, rhos * r)
    hits = ink[ys, xs] & ok
    cov = hits.mean(axis=1)
    cov[ok.mean(axis=1) < 0.6] = 0.0  # ring runs off the page

    if visible is not None and visible.any():
        cov_out = np.where(ok.mean(axis=1) < 0.6, 0.0,
                           hits[:, visible].mean(axis=1))
    else:
        cov_out = cov

    def band(values, lo, hi):
        sel = (rhos >= lo) & (rhos <= hi)
        return float(values[sel].max()) if sel.any() else 0.0

    return (band(cov, *OUTER_BAND), band(cov, *INNER_BAND),
            band(cov_out, *OUTSIDE_BAND))


def rim_metrics(pa, cx, cy, r):
    """Measure the rim: how much is visible, how much of it is radial.

    A stamped circle is a real edge, so crossing it the brightness changes
    along the radial direction.  Angles whose rim sample falls on dark ground
    are treated as occluded rather than as evidence either way.
    """
    radii = np.arange(int(r * RIM_BAND[0]), int(r * RIM_BAND[1]) + 1, dtype=np.float64)
    if radii.size == 0:
        return 0.0, 0.0, 0.0, np.zeros(N_ANGLES, bool)
    xs, ys, ok = _sample(pa.shape, cx, cy, radii)
    mags = pa.mag[ys, xs] * ok
    strongest = mags.argmax(axis=0)
    cols = np.arange(N_ANGLES)
    px, py = xs[strongest, cols], ys[strongest, cols]
    peak = mags[strongest, cols]
    cosine = np.abs(pa.gx[py, px] * _COS + pa.gy[py, px] * _SIN) / np.maximum(peak, 1e-6)

    aligned = (peak > GRAD_MIN) & (cosine > ALIGN_COS)
    visible = (pa.ground[py, px] > PAPER_LEVEL) & ok.any(axis=0)
    if not visible.any():
        return 0.0, 0.0, 0.0, visible
    return (float(visible.mean()),
            float(aligned[visible].mean()),
            _longest_run(aligned | ~visible),
            visible)


def measure(pa, ink, cx, cy, r):
    visible, align, arc, _ = rim_metrics(pa, cx, cy, r)
    # The whitespace test deliberately looks at every angle, occluded ones
    # included.  Restricting it to the visible side was tried and made things
    # worse: it concentrates the measurement on whatever the stamp happens to
    # sit next to, and a stamp half over a chart is better reported honestly
    # as "probable" than argued into a confident pass.
    outer, inner, outside = ring_coverage(ink, cx, cy, r)
    score = 0.45 * align + 0.25 * arc + 0.18 * outer + 0.12 * inner
    return score, dict(outer=outer, inner=inner, outside=outside,
                       visible=visible, align=align, arc=arc)


def _tier(d):
    """'confirmed', 'review' or None."""
    for name, lim in (("confirmed", CONFIRMED), ("review", REVIEW)):
        if (d["outside"] <= lim["outside"] and d["visible"] >= lim["visible"]
                and d["align"] >= lim["align"] and d["arc"] >= lim["arc"]
                and d["outer"] >= lim["outer"] and d["inner"] >= lim["inner"]):
            return name
    return None


def _refine(pa, ink, cx, cy, r):
    """Nudge centre and radius onto the best-scoring circle nearby."""
    score, diag = measure(pa, ink, cx, cy, r)
    best = (score, cx, cy, r, diag)
    step = max(2, int(r * 0.06))
    for _ in range(3):
        improved = False
        for dx, dy, dr in ((step, 0, 0), (-step, 0, 0), (0, step, 0),
                           (0, -step, 0), (0, 0, step), (0, 0, -step)):
            cand = (best[1] + dx, best[2] + dy, best[3] + dr)
            if cand[2] < 8:
                continue
            s, d = measure(pa, ink, *cand)
            if s > best[0] + 1e-4:
                best, improved = (s, *cand, d), True
        if not improved:
            step = max(1, step // 2)
    return best


def _candidates(bgr, pa, dpi):
    """Propose circles on a half-scale image.

    Hough on the full page is an order of magnitude slower and buys no extra
    recall, since every proposal is re-scored and re-centred at full
    resolution anyway.
    """
    sources = [cv2.resize(f.astype(np.uint8) * 255, None, fx=CAND_SCALE,
                          fy=CAND_SCALE, interpolation=cv2.INTER_AREA)
               for _, f in pa.fields]
    sources.append(cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), None,
                              fx=CAND_SCALE, fy=CAND_SCALE,
                              interpolation=cv2.INTER_AREA))
    d = dpi * CAND_SCALE
    r_min, r_max = int(0.26 * d), int(0.85 * d)
    out = []
    for src in sources:
        src = cv2.GaussianBlur(src, (5, 5), 0)
        for p2 in (30, 20):
            circles = cv2.HoughCircles(src, cv2.HOUGH_GRADIENT, dp=1.2,
                                       minDist=int(0.45 * d), param1=110,
                                       param2=p2, minRadius=r_min, maxRadius=r_max)
            if circles is not None:
                out.extend(np.round(circles[0] / CAND_SCALE).astype(int).tolist())
    seen, deduped = set(), []
    for cx, cy, r in out:
        key = (cx // 12, cy // 12, r // 12)
        if key not in seen:
            seen.add(key)
            deduped.append((cx, cy, r))
    return deduped


def find_seals(bgr, dpi=150):
    """Locate round seals on a page.

    Returns a list of dicts sorted best-first, each with x, y, r, a
    'confidence' of "confirmed" or "review", the measured diagnostics, and
    the mean colourfulness of the stamp's ink.
    """
    pa = PageAnalysis(bgr)
    h, w = pa.shape
    mx, my = BORDER_MARGIN * w, BORDER_MARGIN * h

    def in_margin(cx, cy):
        return cx < mx or cx > w - mx or cy < my or cy > h - my

    screened = []
    for cx, cy, r in _candidates(bgr, pa, dpi):
        if cy < LOGO_ZONE[0] * h and cx < LOGO_ZONE[1] * w:
            continue
        if in_margin(cx, cy):
            continue
        # Use the sensitivity that reads this candidate best, but only among
        # those that still see whitespace around the rim: turning sensitivity
        # up always raises coverage, so scoring on the most sensitive map
        # alone would just describe whatever the page is made of.
        best = None
        for name, ink in pa.fields:
            s, diag = measure(pa, ink, cx, cy, r)
            if diag["outside"] > REVIEW["outside"]:
                continue
            if best is None or s > best[0]:
                best = (s, name)
        if best is not None and best[0] >= 0.45:
            screened.append((best[0], cx, cy, r, best[1]))
    screened.sort(key=lambda c: -c[0])

    fields = dict(pa.fields)
    found = []
    for _, cx, cy, r, name in screened[:MAX_REFINE]:
        s, cx, cy, r, diag = _refine(pa, fields[name], cx, cy, r)
        tier = _tier(diag)
        if tier is None or in_margin(cx, cy):
            continue
        dup = next((f for f in found
                    if (cx - f["x"]) ** 2 + (cy - f["y"]) ** 2
                    < (0.7 * max(r, f["r"])) ** 2), None)
        if dup is not None:
            if s <= dup["score"]:
                continue
            found.remove(dup)
        mask = np.zeros(pa.shape, np.uint8)
        cv2.circle(mask, (cx, cy), r, 255, -1)
        found.append(dict(x=int(cx), y=int(cy), r=int(r),
                          score=round(float(s), 3), confidence=tier, field=name,
                          chroma=round(float(cv2.mean(pa.chroma, mask)[0]), 1),
                          **{k: round(float(v), 3) for k, v in diag.items()}))
    found.sort(key=lambda f: (f["confidence"] != "confirmed", -f["score"]))
    return found
