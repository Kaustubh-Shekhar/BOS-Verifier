"""Matching the agenda against what the minutes actually record.

The first page of a BoS lists the agenda for the meeting.  The minutes that
follow should work through those items and record what was decided on each.
This module recovers both lists and lines them up, so an agenda item that was
never minuted - or minuted under a different subject - is reported.

Two layouts occur in practice and both are handled:

  * numbered items under a heading ("AGENDA OF BOS MEETING:" then "1. ...");
  * one item per "Agenda 1", "Agenda 2" marker.

The minutes label their sections the same way, but OCR damages the word
itself surprisingly often - "Agenda no 3" comes back as "Acenda no 3", and on
one sample page the label is lost altogether where the scan clipped the left
margin.  So an item is looked for twice: once by its number, and once by its
subject wording anywhere in the minutes.
"""
import re

# "Agenda 4", "Agenda no. 4" - and, on the sample documents, "Acenda no 3" and
# "Fenda no 1".  OCR mangles the front of the word, so only its tail is
# required; the number that must follow is what keeps this from matching
# ordinary prose.
MARKER_RE = re.compile(
    r"\b\w{1,4}nda\b\s*(?:no\.?|number|item)?\s*[.:\-]?\s*(\d{1,2})\b", re.I)
HEADING_RE = re.compile(r"agenda\b[^:\n]{0,30}:", re.I)
NUMBERED_RE = re.compile(r"(?<![\d.])(\d{1,2})\s*[.)]\s+")

# Where an item's text runs into the page furniture.
STOP_RE = re.compile(
    r"p\.?\s?b\.?\s*no|navrangpura|scanned with|page\s+\d+\s+of|\bhead,|"
    r"principal|www\.|e-?mail|st\.?\s*xavier", re.I)

ITEM_MAX_CHARS = 160
DISCUSSION_CHARS = 420

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "was", "were", "are",
    "has", "have", "had", "not", "but", "its", "their", "them", "they", "will",
    "shall", "may", "any", "all", "per", "also", "than", "then", "there",
    "which", "who", "whom", "been", "being", "into", "onto", "upon", "about",
    "after", "before", "under", "over", "such", "some", "other", "others",
    "agenda", "meeting", "bos", "board", "studies", "study", "held", "date",
    "time", "venue", "members", "member", "point", "points", "following",
    "discussed", "discussion", "presented", "regarding", "sir", "mrs",
}
MIN_WORD = 3

# Procedural items - the welcome, the vote of thanks, "any other business".
# These are always minuted in words of their own, so comparing wording says
# nothing; that such an item was minuted at all is the whole requirement.
PROCEDURAL_RE = re.compile(
    r"vote\s+of\s+thanks|welcome|opening\s+remarks|any\s+other|"
    r"approval\s+of\s+(?:the\s+)?minutes|introduc", re.I)

# How much of an agenda item's wording must reappear for the minutes to be
# treated as plainly covering it.  Minutes paraphrase - "Vote of thanks"
# becomes "thanked the board members" - so falling short of this is a prompt
# to read the page, never a failure on its own.  Only an item that is not
# minuted at all fails.
STRONG_OVERLAP = 0.50
WEAK_OVERLAP = 0.20
# A heading with nothing under it is not a discussion.
MIN_DISCUSSION_CHARS = 25


def _clean(text):
    text = STOP_RE.split(text)[0]
    return " ".join(text.split())[:ITEM_MAX_CHARS].strip(" .,;:-")


def _stem(word):
    """Crude suffix stripping, so 'thanks' and 'thanked' count as the same."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) - len(suffix) >= 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def keywords(text):
    words = re.findall(r"[a-z]+", text.lower())
    return {_stem(w) for w in words if len(w) >= MIN_WORD and w not in STOPWORDS}


def overlap(item_text, against_text):
    """Fraction of an agenda item's distinctive words that reappear."""
    wanted = keywords(item_text)
    if not wanted:
        return 0.0
    return len(wanted & keywords(against_text)) / float(len(wanted))


def _by_markers(text):
    marks = [(int(m.group(1)), m.start(), m.end()) for m in MARKER_RE.finditer(text)]
    items = []
    for i, (number, _, end) in enumerate(marks):
        stop = marks[i + 1][1] if i + 1 < len(marks) else len(text)
        items.append((number, _clean(text[end:stop])))
    return items


def _by_numbering(text):
    m = HEADING_RE.search(text)
    if not m:
        return []
    tail = text[m.end():]
    marks = [(int(x.group(1)), x.start(), x.end()) for x in NUMBERED_RE.finditer(tail)]
    items, expected = [], 1
    for i, (number, _, end) in enumerate(marks):
        if number != expected:
            continue  # out-of-sequence figures are body text, not the list
        stop = marks[i + 1][1] if i + 1 < len(marks) else len(tail)
        items.append((number, _clean(tail[end:stop])))
        expected += 1
    return items


def agenda_list(pages):
    """The agenda as listed at the front of the document.

    Returns (page_number, [(number, subject), ...]).  The list is taken from
    the earliest page that carries one; the minutes reuse the same "Agenda N"
    labels further on, and those are the discussions, not the list.
    """
    for page in pages:
        by_marker = _by_markers(page.text)
        if len({n for n, _ in by_marker}) >= 3:
            seen, items = set(), []
            for number, subject in by_marker:
                if number not in seen:
                    seen.add(number)
                    items.append((number, subject))
            return page.number, sorted(items)
        numbered = _by_numbering(page.text)
        if len(numbered) >= 2:
            return page.number, numbered
    return None, []


def discussions(pages, after_page):
    """Where each agenda number is worked through in the minutes."""
    found = {}
    for page in pages:
        if page.number <= after_page:
            continue
        text = page.text
        marks = [(int(m.group(1)), m.start(), m.end())
                 for m in MARKER_RE.finditer(text)]
        for i, (number, _, end) in enumerate(marks):
            stop = marks[i + 1][1] if i + 1 < len(marks) else len(text)
            body = " ".join(text[end:min(stop, end + DISCUSSION_CHARS)].split())
            if number not in found:
                found[number] = dict(page=page.number, text=body)
    return found


def review(doc):
    """Line the agenda up against the minutes.

    Returns a list of per-item results, each with the item's number, subject,
    what was found, and a verdict of "matched", "loose", "unlabelled",
    "mismatch" or "missing".
    """
    list_page, items = agenda_list(doc.pages)
    if not items:
        return None, []

    body_pages = [p for p in doc.pages if p.number > list_page]
    minutes_text = " ".join(p.text for p in body_pages)
    found = discussions(doc.pages, list_page)

    results = []
    for number, subject in items:
        entry = dict(number=number, subject=subject, page=None, verdict="missing",
                     score=0.0, note="")
        procedural = bool(PROCEDURAL_RE.search(subject))
        entry["procedural"] = procedural
        hit = found.get(number)
        if hit and len(hit["text"]) < MIN_DISCUSSION_CHARS:
            entry.update(page=hit["page"], verdict="empty",
                         note="the minutes carry this heading but record nothing "
                              "under it")
        elif hit:
            score = overlap(subject, hit["text"])
            entry.update(page=hit["page"], score=round(score, 2))
            if procedural:
                entry["verdict"] = "matched"
                entry["note"] = ("procedural item - minuted in its own words, so "
                                 "the wording is not compared")
            elif score >= STRONG_OVERLAP:
                entry["verdict"] = "matched"
            elif score >= WEAK_OVERLAP:
                entry["verdict"] = "loose"
                entry["note"] = ("the minutes paraphrase this item - confirm the "
                                 "discussion really covers it")
            else:
                entry["verdict"] = "divergent"
                entry["note"] = ("what is minuted under this number reads as a "
                                 "different subject: " + hit["text"][:110])
        else:
            # The label may simply not have survived the scan. Look for the
            # subject itself anywhere in the minutes.
            score = overlap(subject, minutes_text)
            entry["score"] = round(score, 2)
            if score >= STRONG_OVERLAP:
                entry["verdict"] = "unlabelled"
                entry["note"] = ("discussed in the minutes, but not under an "
                                 "'Agenda %d' heading the scan could read" % number)
        results.append(entry)
    return list_page, results
