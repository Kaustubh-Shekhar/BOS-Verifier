"""The checklist itself.

Each rule returns a Check with one of four outcomes:

  pass    - the requirement is met, on evidence strong enough to rely on;
  review  - the evidence is there but weaker than a clean pass (a faded seal,
            a stamp whose text did not survive the scan).  Somebody should
            glance at the page; it is not a finding against the document;
  fail    - the requirement is not met;
  n/a     - the rule does not apply to this semester's BoS.

The distinction between "fail" and "review" is the point of the tool.  A scan
of a rubber stamp is not a machine-readable record, and a checker that
pretends otherwise either cries wolf on good documents or waves through bad
ones.
"""
from . import structure

PASS, REVIEW, FAIL, NA = "pass", "review", "fail", "n/a"

# What ends the minutes is the start of another titled section.  "Result" is
# not in this list on purpose: the minutes discuss the results, so a page of
# minutes routinely mentions them, and breaking there cuts the minutes short.
NON_MINUTES_KINDS = {"attendance", "annexure1", "annexure2", "atr"}
# Below this, a page yielded so little text that its stamps cannot be judged
# either way - typically a chart photographed light-on-dark.
READABLE_WORDS = 12
# Above this fraction of non-paper, the page is a dark chart.
DARK_PAGE_COVERAGE = 0.32


class Check:
    def __init__(self, key, title, requirement):
        self.key = key
        self.title = title
        self.requirement = requirement
        self.status = FAIL
        self.detail = ""
        self.pages = []
        self.notes = []

    def set(self, status, detail, pages=None, notes=None):
        self.status = status
        self.detail = detail
        self.pages = pages or []
        self.notes = notes or []
        return self

    def as_dict(self):
        return dict(key=self.key, title=self.title, requirement=self.requirement,
                    status=self.status, detail=self.detail, pages=self.pages,
                    notes=self.notes)


# --- helpers --------------------------------------------------------------

def minutes_pages(doc):
    """The leading run of pages that are the minutes proper.

    The minutes always come first and end where the annexures, the attendance
    sheet or the action-taken report begin.
    """
    out = []
    for page in doc.pages:
        if page.number > 1 and (page.kinds & NON_MINUTES_KINDS):
            break
        out.append(page)
    return out


def _endorsement(page):
    """Does this page carry HOD + Principal + seal, and how firmly?

    Returns (status, missing, notes).  A page the OCR could barely read is
    reported as unverifiable rather than as unsigned: absence of evidence from
    a page that cannot be read is not evidence that the stamps are absent.
    """
    auth = page.authority
    hod, principal = auth["hod"], auth["principal"]
    # A page that is mostly toner - a chart printed light on a dark ground -
    # defeats both OCR and the colour separation the stamp finder relies on.
    dark_chart = page.chart.get("coverage", 0.0) > DARK_PAGE_COVERAGE
    unverifiable = ((page.readable_words < READABLE_WORDS or dark_chart)
                    and not hod["found"] and not principal["found"])
    if unverifiable:
        note = (f"page {page.number} is a chart scan whose stamps could not be "
                "read automatically - confirm the signatures by eye")
        return (FAIL if not page.seals else REVIEW), [], [note]

    missing = []
    if not hod["found"]:
        missing.append("HOD stamp/signature")
    if not principal["found"]:
        missing.append("Principal stamp/signature")
    if not page.seals:
        missing.append("round seal")
    if missing:
        return FAIL, missing, []

    soft = []
    if hod["strength"] != "strong":
        soft.append(f"HOD identified from {hod['how']}")
    if principal["strength"] != "strong":
        soft.append(f"Principal identified from {principal['how']}")
    if page.seal_state == "probable":
        soft.append("the seal is faint or partly overlapped")
    if auth["signatures"] == 0:
        soft.append("no handwritten signature could be separated from the print")
    return (REVIEW if soft else PASS), [], soft


def _worst(statuses):
    for level in (FAIL, REVIEW, PASS):
        if level in statuses:
            return level
    return PASS


# --- the rules ------------------------------------------------------------

def check_letterhead_first_page(doc):
    c = Check("letterhead_first_page", "Letterhead on the first page",
              "Page 1 must be printed on the college letterhead.")
    if not doc.pages:
        return c.set(FAIL, "The document has no pages.")
    first = doc.pages[0]
    if first.letterhead["found"]:
        return c.set(PASS, "Page 1 is on the college letterhead ("
                     + ", ".join(first.letterhead["reasons"]) + ").", [1])
    return c.set(FAIL, "Page 1 does not appear to be on the college letterhead. "
                 "Only a typed heading was found, without the printed crest, "
                 "rule or address footer.", [1])


def check_seal_every_page(doc):
    c = Check("seal_every_page", "Seal on every page",
              "Every page, first to last, must carry the round college seal.")
    missing = [p.number for p in doc.pages if p.seal_state == "missing"]
    probable = [p.number for p in doc.pages if p.seal_state == "probable"]
    if missing:
        return c.set(FAIL, f"No seal found on {len(missing)} of {len(doc.pages)} "
                     f"pages: {_list(missing)}.", missing)
    if probable:
        return c.set(REVIEW, f"All {len(doc.pages)} pages carry a seal, but on "
                     f"{_list(probable)} the impression is faint or overlaps a "
                     "chart, so please confirm by eye.", probable)
    return c.set(PASS, f"All {len(doc.pages)} pages carry the round seal.")


def check_minutes_signed(doc):
    c = Check("minutes_signed", "Minutes signed off at the end",
              "Where the minutes end, the Principal and HOD must sign and seal.")
    pages = minutes_pages(doc)
    if not pages:
        return c.set(FAIL, "The minutes could not be located in this document.")
    last = pages[-1]
    status, missing, soft = _endorsement(last)
    if status is FAIL:
        return c.set(FAIL, f"The minutes end on page {last.number}, which is "
                     f"missing: {_list_words(missing)}.", [last.number])
    return c.set(status, f"The minutes end on page {last.number}, which carries "
                 "the HOD's and Principal's endorsement and the seal.",
                 [last.number], soft)


def check_attendance_letterhead(doc):
    c = Check("attendance_letterhead", "Attendance sheet on letterhead",
              "The attendance sheet must be on the college letterhead.")
    pages = doc.pages_of("attendance")
    if not pages:
        return c.set(FAIL, "No attendance sheet was found in this document.")
    bad = [p.number for p in pages if not p.letterhead["found"]]
    nums = [p.number for p in pages]
    if bad:
        return c.set(FAIL, f"Attendance sheet on {_list(bad)} is not on the "
                     "college letterhead.", bad)
    return c.set(PASS, f"The attendance sheet ({_list(nums)}) is on the college "
                 "letterhead.", nums)


def check_annexure1(doc):
    c = Check("annexure1", "Annexure-1: percentage modification",
              "Annexure-1 must state the percentage of each course modified - "
              "'Nil' if nothing changed - and carry the Principal's and HOD's "
              "sign and seal.")
    pages = doc.pages_of("annexure1")
    if not pages:
        return c.set(FAIL, "No Annexure-1 (percentage modification) was found.")
    page = pages[0]
    courses, nil = structure.courses_in_annexure1(page.text)
    if courses:
        what = f"lists {len(courses)} modified course(s)"
    elif nil:
        what = "records 'Nil'"
    else:
        what = "lists no modified courses, which reads as Nil"

    status, missing, soft = _endorsement(page)
    nums = [p.number for p in pages]
    if status is FAIL:
        return c.set(FAIL, f"Annexure-1 (page {page.number}) {what}, but is "
                     f"missing: {_list_words(missing)}.", nums)
    if not courses and not nil:
        soft = soft + ["the table is empty and the word 'Nil' was not read - "
                       "confirm it is actually stated"]
        status = REVIEW
    return c.set(status, f"Annexure-1 (page {page.number}) {what} and carries "
                 "the required sign and seal.", nums, soft)


def check_annexure2(doc):
    c = Check("annexure2", "Annexure-2: syllabus attached",
              "Every course named in Annexure-1 must have its syllabus "
              "attached in Annexure-2, after Annexure-1.")
    a1_pages = doc.pages_of("annexure1")
    if not a1_pages:
        return c.set(FAIL, "No Annexure-1, so its syllabus copies cannot be "
                     "checked.")
    a1 = a1_pages[0]
    courses, nil = structure.courses_in_annexure1(a1.text)
    if not courses:
        return c.set(PASS, "Annexure-1 modifies no courses, so no syllabus "
                     "copy is required in Annexure-2.", [a1.number])

    after = [p for p in doc.pages if p.number > a1.number
             and (p.kinds & {"annexure2", "syllabus"})]
    if not after:
        return c.set(FAIL, f"Annexure-1 lists {len(courses)} modified course(s) "
                     "but no syllabus is attached after it.", [a1.number])

    attached = []
    for page in after:
        attached.extend(structure.courses_in_annexure2(page.text))
    nums = [p.number for p in after]

    missing = []
    for course in courses:
        needed = sum(1 for x in courses if x["core"] == course["core"])
        have = sum(1 for x in attached if x["core"] == course["core"])
        if have < needed and course["core"] not in [m["core"] for m in missing]:
            missing.append(course)
    if missing:
        return c.set(FAIL, "Annexure-2 does not cover: "
                     + _list_words([m["raw"] for m in missing])
                     + f" (syllabus pages {_list(nums)}).", [a1.number] + nums)
    return c.set(PASS, f"All {len(courses)} course(s) listed in Annexure-1 have "
                 f"a syllabus attached in Annexure-2 (pages {_list(nums)}).",
                 [a1.number] + nums)


def check_odd_semester_results(doc):
    c = Check("odd_result_analysis", "Odd semester: result analysis",
              "An odd-semester BoS must discuss results, with graphs and the "
              "round seal.")
    if doc.semester != "odd":
        return c.set(NA, "This is an even-semester BoS, so result analysis is "
                     "not required here.")
    pages = doc.pages_of("result")
    if not pages:
        return c.set(FAIL, "No result analysis was found.")
    # The minutes discuss the results too. If there are pages given over to the
    # analysis itself, report those rather than every passing mention.
    dedicated = [p for p in pages if not (p.kinds & {"minutes", "atr"})]
    if dedicated:
        pages = dedicated
    charted = [p for p in pages if p.chart["found"]]
    nums = [p.number for p in pages]
    # Graph detection is soft evidence: a grey bar chart photographed off
    # paper does not reliably separate from prose, so its absence is reported
    # for confirmation rather than failed outright.
    graph_note = ([] if charted else
                  ["graphs could not be confirmed automatically on these "
                   "pages - check the charts are actually there"])
    unsealed = [p.number for p in pages if p.seal_state == "missing"]
    faint = [p.number for p in pages if p.seal_state == "probable"]
    if unsealed:
        return c.set(FAIL, f"Result analysis on {_list(unsealed)} carries no "
                     "round seal.", unsealed, graph_note)
    if faint:
        graph_note = graph_note + [f"the seal on {_list(faint)} is faint - "
                                   "confirm by eye"]
    status = REVIEW if graph_note else PASS
    return c.set(status, f"Result analysis on {_list(nums)}"
                 + (f", with graphs detected on {_list([p.number for p in charted])}"
                    if charted else "")
                 + ", carrying the round seal.", nums, graph_note)


def check_even_semester_feedback(doc):
    c = Check("even_feedback", "Even semester: stakeholder feedback",
              "An even-semester BoS must carry feedback from all four "
              "stakeholders - students, alumni, teachers and employers.")
    if doc.semester != "even":
        return c.set(NA, "This is an odd-semester BoS, so stakeholder feedback "
                     "is not required here.")
    found = structure.stakeholders_in(doc.full_text)
    wanted = ["student", "alumni", "teacher", "employer"]
    missing = [w for w in wanted if w not in found]
    pages = [p.number for p in doc.pages if "feedback" in p.kinds]
    if missing:
        return c.set(FAIL, "Stakeholder feedback is missing for: "
                     + _list_words(missing) + ".", pages)
    return c.set(PASS, "Feedback from all four stakeholder groups is present "
                 f"(pages {_list(pages)}).", pages)


def check_action_taken_report(doc):
    c = Check("action_taken_report", "Action Taken Report",
              "An ATR must appear in at least one BoS of the year, with the "
              "Principal's and HOD's sign and seal.")
    pages = doc.pages_of("atr")
    if not pages:
        return c.set(REVIEW, "No Action Taken Report was found in this BoS. That "
                     "is acceptable only if the other BoS for this year carries "
                     "one - please confirm.")
    page = max(pages, key=lambda p: len(p.text))
    status, missing, soft = _endorsement(page)
    nums = [p.number for p in pages]
    if status is FAIL:
        return c.set(FAIL, f"The Action Taken Report (page {page.number}) is "
                     f"missing: {_list_words(missing)}.", nums)
    return c.set(status, f"Action Taken Report on page {page.number}, with the "
                 "required sign and seal.", nums, soft)


def check_even_semester_graphs_signed(doc):
    c = Check("even_graphs_signed", "Even semester: graphs signed",
              "The feedback graphs in an even-semester BoS must carry the HOD's "
              "and Principal's signature with the seal.")
    if doc.semester != "even":
        return c.set(NA, "This is an odd-semester BoS, so feedback graphs are "
                     "not required here.")
    pages = [p for p in doc.pages if "feedback" in p.kinds and p.chart["found"]]
    if not pages:
        # Fall back to the feedback pages themselves: the graph cue is weak,
        # and missing it is not evidence that the graphs are absent.
        pages = [p for p in doc.pages if "feedback" in p.kinds]
    if not pages:
        return c.set(FAIL, "No feedback graphs were found.")
    statuses, notes, bad = [], [], []
    for page in pages:
        status, missing, soft = _endorsement(page)
        statuses.append(status)
        if status is FAIL:
            bad.append(f"page {page.number}: missing {_list_words(missing)}")
        else:
            notes.extend(f"page {page.number}: {s}" for s in soft)
    nums = [p.number for p in pages]
    worst = _worst(statuses)
    if worst is FAIL:
        return c.set(FAIL, "Feedback graphs are not fully endorsed - "
                     + "; ".join(bad) + ".", nums)
    return c.set(worst, f"Feedback graphs on {_list(nums)} carry the required "
                 "endorsement.", nums, notes)


ALL_RULES = [
    check_letterhead_first_page,
    check_seal_every_page,
    check_minutes_signed,
    check_attendance_letterhead,
    check_annexure1,
    check_annexure2,
    check_odd_semester_results,
    check_even_semester_feedback,
    check_action_taken_report,
    check_even_semester_graphs_signed,
]


def run(doc):
    """Run the checklist. Returns the list of Checks, also stored on the doc."""
    doc.checks = [rule(doc) for rule in ALL_RULES]
    return doc.checks


def verdict(checks):
    """The document-level answer."""
    considered = [c for c in checks if c.status != NA]
    if any(c.status == FAIL for c in considered):
        return FAIL
    if any(c.status == REVIEW for c in considered):
        return REVIEW
    return PASS


def _list(numbers):
    numbers = [str(n) for n in numbers]
    if len(numbers) <= 1:
        return numbers[0] if numbers else "none"
    return ", ".join(numbers[:-1]) + " and " + numbers[-1]


def _list_words(words):
    words = list(words)
    if len(words) <= 1:
        return words[0] if words else "nothing"
    return ", ".join(words[:-1]) + " and " + words[-1]
