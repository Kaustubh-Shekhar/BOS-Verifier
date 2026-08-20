"""Working out what each page of a BoS is, and what the document contains.

A BoS runs to a fairly standard shape - minutes, then Annexure-1 (the
percentage of the syllabus modified), then Annexure-2 (the syllabus itself),
an attendance sheet, an action-taken report, and either result analysis or
stakeholder feedback depending on the semester.  The pages are scans with no
bookmarks or tags, so the shape has to be recovered from the text.
"""
import re
from datetime import date

import cv2
import numpy as np

# --- page kinds -----------------------------------------------------------

MINUTES_RE = re.compile(r"minutes\s+of|agenda\s*(no\.?\s*)?\d|opening\s+remarks", re.I)
ANNEX1_RE = re.compile(r"annexure\s*[-–—]?\s*(1|i|l)\b|percentage\s+modification", re.I)
ANNEX2_RE = re.compile(r"annexure\s*[-–—]?\s*(2|ii|il)\b", re.I)
SYLLABUS_RE = re.compile(
    r"course\s*name\s*:|course\s+outcome|course\s+curriculum|tentative\s+syllabus|"
    r"recommended\s+reading|no\.?\s*of\s*credits", re.I)
# "Attendance" alone appears in the body of an action-taken report; only the
# sheet itself is a section.
ATTEND_RE = re.compile(r"attendance\s+sheet", re.I)
ATR_RE = re.compile(r"action\s*[-–—]?\s*taken", re.I)
RESULT_RE = re.compile(r"result\s+analysis|class\s+obtained|grade\s+obtained|"
                       r"result\s+for\s+program", re.I)
FEEDBACK_RE = re.compile(r"feedback", re.I)
STRUCTURE_RE = re.compile(r"programme\s+structure|program\s+structure", re.I)
PAGE_NO_RE = re.compile(r"page\s*(\d{1,2})\s*of\s*(\d{1,2})", re.I)

# --- stakeholders ---------------------------------------------------------

STAKEHOLDERS = {
    "student": re.compile(r"\bstudents?\b", re.I),
    "alumni": re.compile(r"\balumn[iu]s?\b", re.I),
    "teacher": re.compile(r"\b(peer\s*)?teachers?\b|\bfaculty\b", re.I),
    "employer": re.compile(r"\bemployers?\b|\bindustr(y|ial|ies)\b", re.I),
}
FEEDBACK_WINDOW = 220

MONTHS = ("january february march april may june july august september "
          "october november december").split()
DATE_RES = [
    re.compile(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(20\d{2})"),
    re.compile(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\s*,?\s*(20\d{2})"),
    re.compile(r"([A-Za-z]{3,9})\s+(\d{1,2})\s*(?:st|nd|rd|th)?\s*,?\s*(20\d{2})"),
]


# A letterhead plus a title block can fill the top third of a sheet, so the
# heading of the section itself often starts about half way down.
HEADING_BAND = 0.55
# Phrases that mark a *mention* of a section rather than the section itself:
# the agenda lists every item the meeting will cover, and the minutes note
# that the signed attendance sheet is attached.
REFERENCE_CUE = re.compile(
    r"agenda|attached|review\s+of|approval\s+of|discussion\s+on|presented\s+the|"
    r"minutes\s+of\s+the", re.I)
REFERENCE_WINDOW = 70


def classify(page_text):
    """Which kinds of content a page carries. A page can be several.

    What a page *is* comes from its heading.  The minutes mention almost every
    other section in passing - the agenda lists "Review of the Action Taken
    Report", the text notes that a "signed attendance sheet is attached" - and
    reading those as section headings turns the whole document into one
    undifferentiated blur.
    """
    heading = page_text.band(0.0, HEADING_BAND)
    text = page_text.text
    kinds = set()
    # The minutes are the one section that legitimately contains the agenda.
    if MINUTES_RE.search(heading):
        kinds.add("minutes")
    if SYLLABUS_RE.search(text):
        # A syllabus runs over several pages and only the first is headed.
        kinds.add("syllabus")
    for kind, rx in (("annexure1", ANNEX1_RE), ("annexure2", ANNEX2_RE),
                     ("attendance", ATTEND_RE), ("atr", ATR_RE),
                     ("result", RESULT_RE), ("feedback", FEEDBACK_RE),
                     ("programme_structure", STRUCTURE_RE)):
        if _heads(rx, heading):
            kinds.add(kind)
    return kinds


def _heads(rx, heading):
    """Does this page carry the section as its heading, or merely mention it?"""
    for m in rx.finditer(heading):
        lo = max(0, m.start() - REFERENCE_WINDOW)
        window = heading[lo:m.end() + REFERENCE_WINDOW]
        if not REFERENCE_CUE.search(window):
            return True
    return False


def page_numbering(page_text):
    """The 'Page 3 of 10' marker the minutes carry, if present."""
    m = PAGE_NO_RE.search(page_text.text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def meeting_date(pages):
    """The date of the meeting, read from the front of the document."""
    for page in pages[:4]:
        for rx in DATE_RES:
            for m in rx.finditer(page.text):
                parsed = _parse(m)
                if parsed:
                    return parsed
    return None


def _parse(m):
    a, b, c = m.group(1), m.group(2), m.group(3)
    try:
        if a.isdigit() and b.isdigit():
            day, month, year = int(a), int(b), int(c)
        elif a.isdigit():
            day, year = int(a), int(c)
            month = _month(b)
        else:
            day, year = int(b), int(c)
            month = _month(a)
        if not month or not 1 <= day <= 31:
            return None
        return date(year, month, day)
    except (ValueError, TypeError):
        return None


def _month(name):
    name = name.lower()[:3]
    for i, full in enumerate(MONTHS, start=1):
        if full.startswith(name):
            return i
    return None


def semester_of(meeting):
    """Odd or even semester, from when the meeting was held.

    Odd semesters run July-December and even semesters January-June, so a BoS
    held in September reviews the odd semester and one held in March the even.
    """
    if meeting is None:
        return None
    return "odd" if 7 <= meeting.month <= 12 else "even"


def stakeholders_in(text):
    """Which stakeholder groups are named in a feedback context.

    Looking for the words anywhere on the page would match the attendee list,
    so each mention has to sit near the word "feedback".
    """
    found = {}
    low = text.lower()
    for m in re.finditer(r"feedback", low):
        lo = max(0, m.start() - FEEDBACK_WINDOW)
        window = low[lo:m.end() + FEEDBACK_WINDOW]
        for name, rx in STAKEHOLDERS.items():
            if rx.search(window):
                found.setdefault(name, window.strip())
    return found


# --- charts ---------------------------------------------------------------

def has_chart(bgr, dpi=150):
    """Evidence of a graph on this page.

    This is deliberately a weak signal, and the rules treat it as supporting
    evidence rather than as a gate.  Two cues survive contact with real scans:

      * coverage - a chart printed light on a dark ground blackens the sheet;
      * a pie - one large, roughly square block of flat tone.

    Bar detection was tried twice and abandoned.  Counting "solid blocks"
    catches stamps and table cells; requiring bars to share an edge catches
    every left-aligned paragraph on the page.  A grey bar chart photographed
    off paper simply does not separate from prose on shape alone, so rather
    than guess, a result or feedback page with no detected graph is reported
    for a human to confirm.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # Judge against this scan's own paper: these are photographs of paper, and
    # a fixed grey level calls every shadowed page a chart.
    paper = float(np.percentile(gray, 80))
    coverage = float((gray < paper - 45).mean())

    tone = (gray < paper - 22).astype(np.uint8)
    tone[:int(0.22 * bgr.shape[0]), :] = 0  # letterhead furniture, not data
    closed = cv2.morphologyEx(
        tone, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(dpi * 0.04)),) * 2))
    n, _, stats, _ = cv2.connectedComponentsWithStats(closed, 8)

    pie = False
    page_area = bgr.shape[0] * bgr.shape[1]
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w * h > 0.5 * page_area or min(w, h) < 0.9 * dpi:
            continue
        if 0.75 <= w / float(h) <= 1.35 and area / float(w * h) >= 0.62:
            pie = True
            break

    return dict(found=bool(pie or coverage > 0.32), pie=pie,
                coverage=round(coverage, 3))


# --- courses --------------------------------------------------------------

COURSE_NOISE = re.compile(
    r"self\s*financ\w*|grant[\s\-]*in[\s\-]*aid|percentage|modification|remarks|"
    r"course\s*code|course\s*name|sr\.?\s*no|annexure|department\s+of|"
    r"board\s+of\s+studies|st\.?\s*xavier\S*|autonom\w*|ahm\w*|college|"
    r"semester|meeting|\bcore\b|\belective\b|\bnew\b", re.I)
NIL_RE = re.compile(r"\bnil\b|\bnone\b|\bno\s+change", re.I)
PERCENT_RE = re.compile(r"(\d{1,3})\s*%")
# A course title is a handful of words; anything longer is table wreckage.
MAX_TITLE_WORDS = 5
# The OCR text is one long run of words with no line breaks, so the title has
# to be stopped at whatever field follows it rather than at end-of-line.
COURSE_NAME_RE = re.compile(
    r"(?:course\s*)?name\s*[:\-]\s*(.{4,60}?)"
    r"(?=\s*(?:course\s*type|course\s*code|course\s*outcome|no\.?\s*of|"
    r"number\s*of|credits|semester|unit\b|$))", re.I)


def _norm_course(text):
    """Reduce a course title to a comparable core.

    Trailing roman numerals are the first thing OCR mangles - 'Psychology-I'
    comes back as 'Psychology-!' or 'Psychology -l' - so the numeral is
    dropped and courses are matched on the words, with multiplicity.
    """
    cleaned = COURSE_NOISE.sub(" ", text)
    cleaned = re.sub(r"[^a-zA-Z ]+", " ", cleaned)
    keep_short = {"to", "of", "in", "and", "for", "ii", "iv"}
    words = [w for w in cleaned.lower().split()
             if len(w) > 2 or w in keep_short]
    while words and words[-1] in ("i", "ii", "iii", "iv", "l", "il", "ill"):
        words.pop()
    # Only the tail matters: the cell before a percentage also sweeps up the
    # column headings and whatever the OCR made of the table rules.
    return "".join(words[-MAX_TITLE_WORDS:])


# The table reads "<title> | <funding> <n>%".  Anchoring on the funding column
# bounds the title exactly; without it the capture runs back into the page
# heading, which repeats the department name and poisons the comparison.
FUNDED_COURSE_RE = re.compile(
    r"([A-Za-z][A-Za-z\s\-]{6,60}?)\s*[|.,]*\s*"
    r"(?:self\s*financ\w*|grant[\s\-]*in[\s\-]*aid)\s*\d{1,3}\s*%", re.I)


def courses_in_annexure1(text):
    """Course titles listed in the percentage-modification table."""
    if not PERCENT_RE.search(text) and NIL_RE.search(text):
        return [], True

    courses = []
    for m in FUNDED_COURSE_RE.finditer(text):
        core = _norm_course(m.group(1))
        if len(core) >= 8:
            courses.append(dict(raw=m.group(1).strip(), core=core))
    if not courses:
        # No funding column: fall back to the words just before each figure.
        cursor = 0
        for m in PERCENT_RE.finditer(text):
            chunk = text[cursor:m.start()]
            cursor = m.end()
            core = _norm_course(chunk)
            if len(core) >= 8:
                courses.append(dict(raw=chunk.strip()[-60:], core=core))
    return courses, bool(NIL_RE.search(text)) and not courses


def courses_in_annexure2(text):
    """Course titles the attached syllabus covers."""
    out = []
    for m in COURSE_NAME_RE.finditer(text):
        core = _norm_course(m.group(1))
        if len(core) >= 8:
            out.append(dict(raw=m.group(1).strip(), core=core))
    return out
