"""Checking the attendance sheet against the record of who was present.

The front of a BoS carries a roll: the members of the board, with anyone who
did not come marked "(Absent)" - or, in some departments, a plain list headed
"Attendees".  The attendance sheet further back has a signature beside each
name.  The two must agree:

  * a member marked absent must not have signed;
  * a member listed as present must have signed;
  * and if there is no roll at all, nothing can be checked, which is itself a
    finding.

Reading the roll is a text problem.  Deciding whether somebody signed is an
image problem, because a signature is by definition ink the OCR could not
read.  Three things make the image side harder than it looks, and each was
found on the sample sheets:

  * The ruled line a member signs on survives the OCR exactly as a signature
    does, so rules are found and removed first - and found on the untouched
    page, because masking the recognised words cuts every rule a name sits on.
  * Sheets come in two layouts.  Some give each name its own signature line
    at about the height of the name; others are a table with the name centred
    in its row.  The signing space is above the line in the first and the row
    itself in the second, and using the wrong shape puts one member's
    signature in the next member's cell.
  * A slightly skewed scan moves a one-pixel table rule between pixel rows, so
    it breaks into pieces too short to recognise unless it is thickened first.

On the two sample documents this separates cleanly: of 28 members across four
sheets, every signed row reads at least 2.1% ink and every unsigned row at
most 0.8%.
"""
import re

import cv2
import numpy as np

from .imaging import ink_layers

# --- reading the roll ------------------------------------------------------

HONORIFIC = r"(?:Prof\.?\s*\(\s*Dr\.?\s*\)|Dr|Prof|Mrs|Mr|Ms|Shri|Smt)"
_NEXT_HONORIFIC = r"(?:Dr|Prof|Mrs|Mr|Ms|Shri|Smt)\b"
# A title, then up to six name parts: words or initials.  The lookahead stops
# a name running on into the next member's title, since OCR flattens the list
# into one line: "Dr. B. D. Dhila Dr. Jigar Parikh (Absent)".
NAME_RE = re.compile(
    r"\b" + HONORIFIC + r"\.?\s*"
    r"((?:(?!" + _NEXT_HONORIFIC + r")(?:[A-Z][A-Za-z'\-]+|[A-Z](?![a-z]))\.?\s*){1,6})")
ABSENT_RE = re.compile(r"\s*[\(\[]\s*absent", re.I)
ROLL_CUE = re.compile(r"\bpresent\b|\babsent\b|\battendees\b|\battended\b", re.I)
ROLL_SEARCH_PAGES = 5
MIN_ROLL = 4

# --- deciding signatures ---------------------------------------------------

SIGNED_INK = 0.016
UNSIGNED_INK = 0.010
WORK_DPI = 150


def _split_camel(text):
    """"AchintaYagnik" -> "Achinta Yagnik"."""
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", text)


def tokens(text):
    """Comparable name parts: lower-case words, initials split out."""
    text = _split_camel(text)
    out = []
    for part in re.split(r"[\s.,;:()_\-|'`]+", text):
        if not part or not part.isalpha():
            continue
        if part.isupper() and 1 < len(part) <= 4:
            out.extend(part)                               # "MN", "MLN" -> initials
        else:
            out.append(part)
    return [t.lower() for t in out]


def members_on(text):
    """Members named on a page, with whether each is marked absent."""
    out = []
    for m in NAME_RE.finditer(text):
        parts = tokens(m.group(1))
        if not parts or max(len(p) for p in parts) < 3:
            continue
        # OCR sometimes runs a first name into the surname ("AchintaYagnik").
        name = _split_camel(" ".join(m.group(1).split()))
        out.append(dict(
            name=name,
            tokens=parts,
            absent=bool(ABSENT_RE.match(text, m.end())),
        ))
    return out


def find_roll(pages):
    """The page recording who was present, and the members on it.

    It is near the front, says so ("present", "absent", "attendees"), and names
    more members than any other such page.  The attendance sheet itself is
    excluded: it names everyone too, but it is the thing being checked.
    """
    best = (None, [])
    for page in pages[:ROLL_SEARCH_PAGES]:
        if "attendance" in page.kinds or not ROLL_CUE.search(page.text):
            continue
        found = members_on(page.text)
        if len(found) >= MIN_ROLL and len(found) > len(best[1]):
            best = (page.number, found)
    return best


# --- measuring a sheet -------------------------------------------------------

def _lines(page_text, ocr_scale):
    """OCR words grouped into lines of text, in working-image coordinates."""
    words = sorted((w for w in page_text.words
                    if w["conf"] >= 20 and w["text"].strip()),
                   key=lambda w: w["y"] + w["h"] / 2.0)
    lines = []
    for w in words:
        cy = (w["y"] + w["h"] / 2.0) / ocr_scale
        h = w["h"] / ocr_scale
        entry = dict(text=w["text"], x0=w["x"] / ocr_scale,
                     x1=(w["x"] + w["w"]) / ocr_scale)
        for line in lines:
            if abs(line["cy"] - cy) < 0.6 * max(line["h"], h):
                line["words"].append(entry)
                line["_ys"].append(cy)
                line["cy"] = float(np.mean(line["_ys"]))
                break
        else:
            lines.append(dict(words=[entry], cy=cy, h=h, _ys=[cy]))
    for line in lines:
        del line["_ys"]
    return lines


def measure_sheet(bgr, page_text, ocr_scale, dpi=WORK_DPI):
    """Everything needed to judge signatures on this sheet later.

    The roll may not have been read yet when the sheet is reached, so the
    matching happens after the whole document is in.  What is kept is small:
    the signature-evidence mask, packed to bits, the rules and the text lines.
    """
    chroma, dark = ink_layers(bgr)

    # Rules, from the untouched page.
    raw = ((dark > 25) | (chroma > 40)).astype(np.uint8)
    thick = cv2.dilate(raw, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
    horizontal = cv2.morphologyEx(
        thick, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (int(0.5 * dpi), 1)))
    vertical = cv2.morphologyEx(
        raw, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(0.3 * dpi))))
    n, _, stats, _ = cv2.connectedComponentsWithStats(horizontal, 8)
    rules = [dict(x1=int(stats[i][0] + stats[i][2]),
                  y=int(stats[i][1] + stats[i][3] / 2))
             for i in range(1, n)
             if stats[i][2] >= 0.9 * dpi and stats[i][3] <= 10]

    # Signature evidence: ink the OCR did not read, with the rules taken out.
    ink = ((dark > 40) | (chroma > 60)).astype(np.uint8)
    pad = max(2, int(0.02 * dpi))
    for w in page_text.confident_words(min_conf=55):
        x, y = int(w["x"] / ocr_scale), int(w["y"] / ocr_scale)
        ww, hh = int(w["w"] / ocr_scale), int(w["h"] / ocr_scale)
        cv2.rectangle(ink, (x - pad, y - pad), (x + ww + pad, y + hh + pad), 0, -1)
    lines_mask = cv2.dilate(cv2.bitwise_or(horizontal, vertical),
                            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    ink = cv2.subtract(ink, lines_mask)

    return dict(shape=ink.shape, bits=np.packbits(ink > 0), rules=rules,
                lines=_lines(page_text, ocr_scale), dpi=dpi)


# --- matching the roll to a sheet --------------------------------------------

def _distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _same(member_part, sheet_part):
    """One name part against one word on the sheet, tolerating one OCR slip."""
    if len(member_part) == 1:
        return member_part == sheet_part
    if member_part == sheet_part:
        return True
    return (len(member_part) >= 4 and abs(len(member_part) - len(sheet_part)) <= 1
            and _distance(member_part, sheet_part) <= 1)


def _score(member, line):
    """How well a line of the sheet names this member, and where the name ends.

    The surname must be there.  The rest of the name then settles which of
    several same-surname members this is: the sample sheet has three Patels.
    """
    parts = [(t, w) for w in line["words"] for t in tokens(w["text"])]
    surname = [t for t in member["tokens"] if len(t) >= 3][-1]
    if not any(_same(surname, t) for t, _ in parts):
        return 0.0, None
    matched, end = 0, None
    for want in member["tokens"]:
        for got, word in parts:
            if _same(want, got):
                matched += 1
                end = max(end or 0, word["x1"])
                break
    return matched / float(len(member["tokens"])), end


def _rule_ys(rules, name_end, dpi):
    """Heights of rules that reach out to the right of a name, de-duplicated."""
    ys = []
    for y in sorted(r["y"] for r in rules if r["x1"] > name_end + 0.3 * dpi):
        if not ys or y - ys[-1] > 5:
            ys.append(y)
    return ys


def _read_sheet(sheet, members):
    """Signature ink in each member's cell on one sheet."""
    dpi = sheet["dpi"]
    lines = sheet["lines"]

    candidates = []
    for mi, member in enumerate(members):
        for li, line in enumerate(lines):
            score, end = _score(member, line)
            if score > 0:
                candidates.append((score, mi, li, end))
    candidates.sort(key=lambda c: -c[0])
    placed, used_lines = {}, set()
    for score, mi, li, end in candidates:
        if mi in placed or li in used_lines:
            continue
        placed[mi] = (lines[li]["cy"], end)
        used_lines.add(li)
    if not placed:
        return {}

    centres = sorted(cy for cy, _ in placed.values())
    gaps = [b - a for a, b in zip(centres, centres[1:]) if 0.1 * dpi < b - a < 1.0 * dpi]
    row = float(np.median(gaps)) if gaps else 0.4 * dpi

    # Which layout?  If the names sit at about the height of their own rules
    # it is one signature line per name; otherwise treat it as table rows.
    offsets = []
    for cy, end in placed.values():
        ys = _rule_ys(sheet["rules"], end, dpi)
        if ys:
            offsets.append(min(abs(y - cy) for y in ys) / row)
    per_line = len(offsets) >= max(3, len(placed) // 2) and np.median(offsets) < 0.3

    ink = np.unpackbits(sheet["bits"])[:sheet["shape"][0] * sheet["shape"][1]]
    ink = ink.reshape(sheet["shape"])
    height, width = ink.shape

    out = {}
    for mi, (cy, end) in placed.items():
        top, bottom = cy - 0.5 * row, cy + 0.5 * row
        if per_line:
            ys = _rule_ys(sheet["rules"], end, dpi)
            if ys:
                line_y = min(ys, key=lambda y: abs(y - cy))
                if abs(line_y - cy) < 0.45 * row:
                    # A signature is written on its line, so the space that
                    # belongs to this member is the band just above it.
                    top, bottom = line_y - 0.85 * row, line_y + 0.15 * row
        left = int(end + 0.15 * dpi)
        cell = ink[max(0, int(top)):min(height, int(bottom)), left:width - int(0.02 * width)]
        out[mi] = float(cell.mean()) if cell.size else 0.0
    return out


def review(doc):
    """Compare the roll with the signatures, across every attendance sheet.

    Returns a dict: the roll's page (or None), the sheets checked, and each
    member with whether they were marked absent, what the sheets show, and
    the page to look at.
    """
    roll_page, members = find_roll(doc.pages)
    sheets = [p for p in doc.pages
              if "attendance" in p.kinds and getattr(p, "_sheet", None)]
    result = dict(roll_page=roll_page, sheets=[p.number for p in sheets], members=[])
    if roll_page is None:
        return result

    readings = {mi: [] for mi in range(len(members))}
    for page in sheets:
        for mi, ink in _read_sheet(page._sheet, members).items():
            readings[mi].append((ink, page.number))

    for mi, member in enumerate(members):
        seen = readings[mi]
        entry = dict(name=member["name"], absent=member["absent"],
                     pages=sorted({p for _, p in seen}), ink=None, page=None)
        if not seen:
            entry["signature"] = "not_found"
        else:
            # Duplicate sheets are common.  The strongest reading decides: a
            # signature on any copy counts as signed.
            ink, page = max(seen)
            entry.update(ink=round(ink, 4), page=page)
            if ink >= SIGNED_INK:
                entry["signature"] = "signed"
            elif ink <= UNSIGNED_INK:
                entry["signature"] = "unsigned"
            else:
                entry["signature"] = "unclear"
        result["members"].append(entry)
    return result
