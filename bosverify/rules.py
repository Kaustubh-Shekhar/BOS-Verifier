"""The checklist itself.

Each rule returns a Check with one of four outcomes:

  pass    - the requirement is met, on evidence strong enough to rely on;
  review  - the evidence is there but weaker than a clean pass (a faded seal,
            a stamp whose text did not survive the scan).  Somebody should
            glance at the page; it is not a finding against the document;
  fail    - the requirement is not met;
  n/a     - the rule does not apply to this BoS.

The distinction between "fail" and "review" is the point of the tool.  A scan
of a rubber stamp is not a machine-readable record, and a checker that
pretends otherwise either cries wolf on good documents or waves through bad
ones.

A check in review carries *items*: the individual things a person has to look
at, usually one per page.  Each can be marked pass or fail by the person
checking, and the check's outcome follows from those decisions - any failed
item fails it, all items passed passes it.  A failed check carries *failures*:
the page and a plain statement of what is wrong, which is what the generated
report is built from.
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


def item(page, confirm, problem):
    """Something a person must look at: what to confirm, and what it means if
    it turns out not to hold."""
    return dict(page=page, label=confirm, problem=problem)


def failure(page, issue):
    return dict(page=page, issue=issue)


class Check:
    def __init__(self, key, title, requirement):
        self.key = key
        self.title = title
        self.requirement = requirement
        self.status = FAIL
        self.detail = ""
        self.pages = []
        self.notes = []
        self.items = []
        self.failures = []
        self.table = None

    def set(self, status, detail, pages=None, notes=None, items=None, failures=None):
        self.status = status
        self.detail = detail
        self.pages = pages or []
        self.notes = notes or []
        # Items only mean something while a check awaits a decision, and
        # failures only once it has failed.
        self.items = (items or []) if status == REVIEW else []
        self.failures = (failures or []) if status == FAIL else []
        if status == REVIEW and not self.items:
            # Every check in review must be resolvable by a person.
            self.items = [item(self.pages[0] if len(self.pages) == 1 else None,
                               "Confirm this by eye: " + detail,
                               "Not confirmed on review: " + detail)]
        if status == FAIL and not self.failures:
            self.failures = [failure(self.pages[0] if len(self.pages) == 1 else None,
                                     detail)]
        for i, entry in enumerate(self.items):
            entry["id"] = "i%d" % i
        return self

    def as_dict(self):
        return dict(key=self.key, title=self.title, requirement=self.requirement,
                    status=self.status, detail=self.detail, pages=self.pages,
                    notes=self.notes, items=self.items, failures=self.failures,
                    table=self.table)


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
        if not page.seals:
            return FAIL, ["round seal"], [note]
        return REVIEW, [], [note]

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


def _endorsement_item(page, what):
    return item(page, f"Confirm the HOD's and Principal's signatures, stamps and "
                      f"the round seal are on {what}",
                f"{what[0].upper() + what[1:]} is not signed and sealed by the "
                "HOD and Principal")


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
                 "rule or address footer.", [1],
                 failures=[failure(1, "Page 1 is not on the printed college letterhead")])


def check_seal_every_page(doc):
    c = Check("seal_every_page", "Seal on every page",
              "Every page, first to last, must carry the round college seal.")
    missing = [p.number for p in doc.pages if p.seal_state == "missing"]
    probable = [p.number for p in doc.pages if p.seal_state == "probable"]
    if missing:
        return c.set(FAIL, f"No seal found on {len(missing)} of {len(doc.pages)} "
                     f"pages: {_list(missing)}.", missing,
                     failures=[failure(n, "No round college seal on this page")
                               for n in missing])
    if probable:
        return c.set(REVIEW, f"All {len(doc.pages)} pages carry a seal, but on "
                     f"{_list(probable)} the impression is faint or overlaps a "
                     "chart, so please confirm by eye.", probable,
                     items=[item(n, "Confirm the round college seal is on this page",
                                 "The round college seal is missing or not legible")
                            for n in probable])
    return c.set(PASS, f"All {len(doc.pages)} pages carry the round seal.")


def check_minutes_signed(doc):
    c = Check("minutes_signed", "Minutes signed off at the end",
              "Where the minutes end, the Principal and HOD must sign and seal.")
    pages = minutes_pages(doc)
    if not pages:
        return c.set(FAIL, "The minutes could not be located in this document.",
                     failures=[failure(None, "The minutes could not be found")])
    last = pages[-1]
    status, missing, soft = _endorsement(last)
    if status is FAIL:
        return c.set(FAIL, f"The minutes end on page {last.number}, which is "
                     f"missing: {_list_words(missing)}.", [last.number],
                     failures=[failure(last.number, "The minutes end here, but this "
                                       f"page is missing the {_list_words(missing)}")])
    return c.set(status, f"The minutes end on page {last.number}, which carries "
                 "the HOD's and Principal's endorsement and the seal.",
                 [last.number], soft,
                 items=[_endorsement_item(last.number, "the last page of the minutes")])


def check_agenda_minuted(doc):
    c = Check("agenda_minuted", "Agenda items covered in the minutes",
              "Every item on the agenda must be minuted, and what is recorded "
              "under each must be that item.")
    items = doc.agenda_items
    if not items:
        return c.set(REVIEW, "No agenda list could be read from the front of "
                     "the document, so the minutes could not be checked against "
                     "it. Confirm the agenda is present and legible.",
                     items=[item(None, "Confirm the agenda is listed and every item "
                                       "on it is minuted",
                                 "The agenda is missing, or its items are not minuted")])

    missing = [i for i in items if i["verdict"] in ("missing", "empty")]
    divergent = [i for i in items if i["verdict"] == "divergent"]
    soft = [i for i in items if i["verdict"] in ("loose", "unlabelled")]
    pages = sorted({i["page"] for i in items if i["page"]})

    def short(i):
        return f"agenda item {i['number']} ({i['subject'][:70]})"

    if missing:
        detail = "; ".join(
            short(i) + (" has a heading but nothing minuted under it"
                        if i["verdict"] == "empty" else " is not minuted anywhere")
            for i in missing)
        return c.set(FAIL, f"{len(missing)} of {len(items)} agenda items listed "
                     f"on page {doc.agenda_page} are not covered: {detail}.",
                     pages or [doc.agenda_page],
                     failures=[failure(i["page"],
                                       short(i)[0].upper() + short(i)[1:]
                                       + (" has a heading but nothing is minuted under it"
                                          if i["verdict"] == "empty"
                                          else " is not minuted"))
                               for i in missing])

    notes = [f"item {i['number']}: {i['note']}" for i in divergent + soft if i["note"]]
    to_check = [item(i["page"], f"Confirm {short(i)} is minuted, and that what is "
                                "recorded is that item",
                     short(i)[0].upper() + short(i)[1:] + " is not properly minuted")
                for i in divergent + soft]
    if divergent:
        return c.set(REVIEW, f"All {len(items)} agenda items are minuted, but "
                     f"{len(divergent)} record a subject that does not read like "
                     "the agenda item - check these pages.", pages, notes, to_check)
    if soft:
        return c.set(REVIEW, f"All {len(items)} agenda items listed on page "
                     f"{doc.agenda_page} are minuted.", pages, notes, to_check)
    return c.set(PASS, f"All {len(items)} agenda items listed on page "
                 f"{doc.agenda_page} are minuted, and each discussion matches "
                 "its item.", pages)


def check_attendance_letterhead(doc):
    c = Check("attendance_letterhead", "Attendance sheet on letterhead",
              "The attendance sheet must be on the college letterhead.")
    pages = doc.pages_of("attendance")
    if not pages:
        return c.set(FAIL, "No attendance sheet was found in this document.",
                     failures=[failure(None, "There is no attendance sheet")])
    bad = [p.number for p in pages if not p.letterhead["found"]]
    nums = [p.number for p in pages]
    if bad:
        return c.set(FAIL, f"Attendance sheet on {_list(bad)} is not on the "
                     "college letterhead.", bad,
                     failures=[failure(n, "The attendance sheet is not on the college "
                                          "letterhead") for n in bad])
    return c.set(PASS, f"The attendance sheet ({_list(nums)}) is on the college "
                 "letterhead.", nums)


SIGNATURE_WORDS = dict(signed="Signed", unsigned="Not signed",
                       unclear="Unclear", not_found="Not on the sheet")


def check_roll_call(doc):
    c = Check("roll_call", "Attendance matches who was present",
              "There must be a page recording which members were present and which "
              "were absent. A member marked absent must not have signed the "
              "attendance sheet, and a member listed as present must have signed it.")
    roll = doc.roll
    rp = roll["roll_page"]
    if rp is None:
        return c.set(FAIL, "No page was found recording which members were present "
                     "and which were absent - a list of the board's members with "
                     "absentees marked, or a list of attendees. Without it the "
                     "attendance sheet cannot be checked.",
                     failures=[failure(None, "There is no page recording which "
                                             "members were present and which were "
                                             "absent")])

    members = roll["members"]
    sheets = roll["sheets"]
    present = [m for m in members if not m["absent"]]
    absent = [m for m in members if m["absent"]]

    fails, items, rows = [], [], []
    for m in members:
        state = "Absent" if m["absent"] else "Present"
        sig = m["signature"]
        where = m["page"] or (sheets[0] if sheets else None)
        on = m["pages"] or sheets
        sheet = ("the attendance sheet (page%s %s)" % ("s" if len(on) > 1 else "", _list(on))
                 if on else "the attendance sheet")
        outcome = "ok"
        if m["absent"]:
            if sig == "signed":
                outcome = "problem"
                fails.append(failure(where, f"{m['name']} is marked absent on page "
                                            f"{rp}, but has signed {sheet}"))
            elif sig == "unclear":
                outcome = "confirm"
                items.append(item(where, f"Confirm {m['name']} (marked absent) has "
                                         f"NOT signed {sheet}",
                                  f"{m['name']} is marked absent on page {rp}, but "
                                  f"has signed {sheet}"))
        else:
            if sig == "unsigned":
                outcome = "problem"
                fails.append(failure(where, f"{m['name']} is listed as present on page "
                                            f"{rp}, but has not signed {sheet}"))
            elif sig == "unclear":
                outcome = "confirm"
                items.append(item(where, f"Confirm {m['name']} (present) has signed "
                                         f"{sheet}",
                                  f"{m['name']} is listed as present on page {rp}, "
                                  f"but has not signed {sheet}"))
            elif sig == "not_found":
                outcome = "confirm"
                items.append(item(where, f"Find {m['name']} (present) on the "
                                         "attendance sheet and confirm they signed",
                                  f"{m['name']} is listed as present on page {rp}, "
                                  "but is not on the attendance sheet or has not "
                                  "signed it"))
        rows.append(dict(name=m["name"], marked=state,
                         signature=SIGNATURE_WORDS[sig] if sheets else "—",
                         outcome=outcome, page=m["page"]))
    c.table = rows

    if not sheets:
        return c.set(FAIL, f"Page {rp} lists {len(members)} members, but there is no "
                     "attendance sheet to check their signatures against.", [rp],
                     failures=[failure(None, "There is no attendance sheet to check "
                                             "the members' signatures against")])

    pages = sorted({rp, *sheets})
    if fails:
        return c.set(FAIL, f"Page {rp} lists {len(members)} members "
                     f"({len(present)} present, {len(absent)} absent). "
                     f"{len(fails)} do not match the attendance sheet.",
                     pages, failures=fails,
                     notes=[f"also to confirm: {i['label']}" for i in items])
    if items:
        return c.set(REVIEW, f"Page {rp} lists {len(members)} members "
                     f"({len(present)} present, {len(absent)} absent). No clear "
                     f"mismatch with the attendance sheet, but {len(items)} "
                     f"{'row needs' if len(items) == 1 else 'rows need'} a look.",
                     pages, items=items)
    return c.set(PASS, f"Page {rp} lists {len(members)} members. All "
                 f"{len(present)} present members signed the attendance sheet"
                 + (f", and none of the {len(absent)} absent members did"
                    if absent else "")
                 + f" (attendance sheet{'s' if len(sheets) > 1 else ''} on "
                   f"page{'s' if len(sheets) > 1 else ''} {_list(sheets)}).", pages)


def check_annexure1(doc):
    c = Check("annexure1", "Annexure-1: percentage modification",
              "Annexure-1 must state the percentage of each course modified - "
              "'Nil' if nothing changed - and carry the Principal's and HOD's "
              "sign and seal.")
    pages = doc.pages_of("annexure1")
    if not pages:
        return c.set(FAIL, "No Annexure-1 (percentage modification) was found.",
                     failures=[failure(None, "There is no Annexure-1 (percentage "
                                             "modification)")])
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
                     f"missing: {_list_words(missing)}.", nums,
                     failures=[failure(page.number, "Annexure-1 is missing the "
                                                    f"{_list_words(missing)}")])
    to_check = [item(page.number, "Confirm Annexure-1 states the percentage modified "
                                  "(or Nil) and carries the HOD's and Principal's "
                                  "signatures, stamps and the seal",
                     "Annexure-1 is incomplete, or not signed and sealed by the HOD "
                     "and Principal")]
    if not courses and not nil:
        soft = soft + ["the table is empty and the word 'Nil' was not read - "
                       "confirm it is actually stated"]
        status = REVIEW
    return c.set(status, f"Annexure-1 (page {page.number}) {what} and carries "
                 "the required sign and seal.", nums, soft, to_check)


def check_annexure2(doc):
    c = Check("annexure2", "Annexure-2: syllabus attached",
              "Every course named in Annexure-1 must have its syllabus "
              "attached in Annexure-2, after Annexure-1.")
    a1_pages = doc.pages_of("annexure1")
    if not a1_pages:
        return c.set(FAIL, "No Annexure-1, so its syllabus copies cannot be "
                     "checked.",
                     failures=[failure(None, "Annexure-2 cannot be checked, because "
                                             "there is no Annexure-1")])
    a1 = a1_pages[0]
    courses, nil = structure.courses_in_annexure1(a1.text)
    if not courses:
        return c.set(PASS, "Annexure-1 modifies no courses, so no syllabus "
                     "copy is required in Annexure-2.", [a1.number])

    after = [p for p in doc.pages if p.number > a1.number
             and (p.kinds & {"annexure2", "syllabus"})]
    if not after:
        return c.set(FAIL, f"Annexure-1 lists {len(courses)} modified course(s) "
                     "but no syllabus is attached after it.", [a1.number],
                     failures=[failure(a1.number, f"Annexure-1 lists {len(courses)} "
                                                  "modified course(s), but no syllabus "
                                                  "is attached (Annexure-2 is missing)")])

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
        names = _list_words([m["raw"] for m in missing])
        return c.set(FAIL, "Annexure-2 does not cover: " + names
                     + f" (syllabus pages {_list(nums)}).", [a1.number] + nums,
                     failures=[failure(a1.number, "No syllabus attached in Annexure-2 "
                                                  f"for: {names}")])
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
        return c.set(FAIL, "No result analysis was found.",
                     failures=[failure(None, "There is no result analysis (required "
                                             "in an odd-semester BoS)")])
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
                     "round seal.", unsealed, graph_note,
                     failures=[failure(n, "The result analysis page has no round seal")
                               for n in unsealed])
    if faint:
        graph_note = graph_note + [f"the seal on {_list(faint)} is faint - "
                                   "confirm by eye"]
    status = REVIEW if graph_note else PASS
    to_check = [item(n, "Confirm the result analysis graphs and the round seal are "
                        "on this page",
                     "Result analysis graphs or the round seal are missing")
                for n in (nums if not charted else faint)]
    return c.set(status, f"Result analysis on {_list(nums)}"
                 + (f", with graphs detected on {_list([p.number for p in charted])}"
                    if charted else "")
                 + ", carrying the round seal.", nums, graph_note, to_check)


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
                     + _list_words(missing) + ".", pages,
                     failures=[failure(None, "No stakeholder feedback from: "
                                             + _list_words(missing))])
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
                     "one - please confirm.",
                     items=[item(None, "Confirm the other BoS for this year carries a "
                                       "signed and sealed Action Taken Report",
                                 "There is no Action Taken Report in this BoS or in "
                                 "the other BoS for the year")])
    page = max(pages, key=lambda p: len(p.text))
    status, missing, soft = _endorsement(page)
    nums = [p.number for p in pages]
    if status is FAIL:
        return c.set(FAIL, f"The Action Taken Report (page {page.number}) is "
                     f"missing: {_list_words(missing)}.", nums,
                     failures=[failure(page.number, "The Action Taken Report is "
                                                    f"missing the {_list_words(missing)}")])
    return c.set(status, f"Action Taken Report on page {page.number}, with the "
                 "required sign and seal.", nums, soft,
                 [_endorsement_item(page.number, "the Action Taken Report")])


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
        return c.set(FAIL, "No feedback graphs were found.",
                     failures=[failure(None, "There are no stakeholder feedback graphs "
                                             "(required in an even-semester BoS)")])
    statuses, notes, bad, to_check = [], [], [], []
    for page in pages:
        status, missing, soft = _endorsement(page)
        statuses.append(status)
        if status is FAIL:
            bad.append(failure(page.number, "The feedback graph is missing the "
                                            f"{_list_words(missing)}"))
        elif status is REVIEW:
            notes.extend(f"page {page.number}: {s}" for s in soft)
            to_check.append(_endorsement_item(page.number, "this feedback graph"))
    nums = [p.number for p in pages]
    worst = _worst(statuses)
    if worst is FAIL:
        return c.set(FAIL, "Feedback graphs are not fully endorsed on "
                     + _list([f["page"] for f in bad]) + ".", nums, failures=bad)
    return c.set(worst, f"Feedback graphs on {_list(nums)} carry the required "
                 "endorsement.", nums, notes, to_check)


ALL_RULES = [
    check_letterhead_first_page,
    check_seal_every_page,
    check_minutes_signed,
    check_agenda_minuted,
    check_attendance_letterhead,
    check_roll_call,
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


# --- a person's decisions ---------------------------------------------------

def status_after(check, decisions):
    """A check's outcome once the person checking has made their decisions.

    Only checks in review can be changed.  Any item marked fail fails the
    check; every item marked pass passes it; anything still undecided leaves
    it in review.
    """
    if check.status != REVIEW:
        return check.status
    marks = decisions.get(check.key, {})
    values = [marks.get(i["id"]) for i in check.items]
    if "fail" in values:
        return FAIL
    if values and all(v == "pass" for v in values):
        return PASS
    return REVIEW


def verdict(checks, decisions=None):
    """The document-level answer."""
    decisions = decisions or {}
    statuses = [status_after(c, decisions) for c in checks]
    considered = [s for s in statuses if s != NA]
    if FAIL in considered:
        return FAIL
    if REVIEW in considered:
        return REVIEW
    return PASS


def tally(checks, decisions=None):
    decisions = decisions or {}
    statuses = [status_after(c, decisions) for c in checks]
    return {level: statuses.count(level) for level in (PASS, REVIEW, FAIL, NA)}


def summary(doc, checks, decisions=None):
    """The plain-language report: what is wrong, page by page.

    Built from the automatic failures and from every item a person marked as
    failing, so it reflects the decisions made on the report page.
    """
    decisions = decisions or {}
    by_page, general, confirm = {}, [], []
    manual_passes = manual_fails = 0

    def add(page, check, issue, manual=False):
        entry = dict(check=check.title, issue=issue, manual=manual)
        if page:
            by_page.setdefault(page, []).append(entry)
        else:
            general.append(entry)

    for c in checks:
        if c.status == FAIL:
            for f in c.failures:
                add(f["page"], c, f["issue"])
        elif c.status == REVIEW:
            marks = decisions.get(c.key, {})
            for it in c.items:
                decision = marks.get(it["id"])
                if decision == "fail":
                    manual_fails += 1
                    add(it["page"], c, it["problem"], manual=True)
                elif decision == "pass":
                    manual_passes += 1
                else:
                    confirm.append(dict(page=it["page"], check=c.title,
                                        label=it["label"]))

    statuses = {c.key: status_after(c, decisions) for c in checks}
    return dict(
        verdict=verdict(checks, decisions),
        counts=tally(checks, decisions),
        pages=[dict(page=n, issues=by_page[n]) for n in sorted(by_page)],
        general=general,
        confirm=confirm,
        passed=[c.title for c in checks if statuses[c.key] == PASS],
        not_applicable=[c.title for c in checks if statuses[c.key] == NA],
        problems=sum(len(v) for v in by_page.values()) + len(general),
        manual_passes=manual_passes,
        manual_fails=manual_fails,
    )


def summary_text(doc, report, filename):
    """The same summary as plain text, for copying into an email."""
    words = {PASS: "PASSABLE", REVIEW: "PASSABLE, WITH POINTS TO CONFIRM",
             FAIL: "NOT PASSABLE"}
    lines = [f"BoS check summary: {filename}"]
    facts = [f"{len(doc.pages)} pages"]
    if doc.meeting_date:
        facts.append("meeting of " + doc.meeting_date.strftime("%d %B %Y"))
    if doc.semester:
        facts.append(f"{doc.semester} semester")
    lines.append(", ".join(facts))
    lines.append("")
    n = report["problems"]
    lines.append(f"Overall result: {words[report['verdict']]}"
                 + (f" - {n} problem{'s' if n != 1 else ''} found" if n else ""))
    if report["pages"]:
        lines += ["", "PAGES WITH PROBLEMS"]
        for entry in report["pages"]:
            lines.append(f"Page {entry['page']}")
            for issue in entry["issues"]:
                lines.append(f"  - {issue['issue']}"
                             + (" (failed on review)" if issue["manual"] else ""))
    if report["general"]:
        lines += ["", "PROBLEMS WITH THE DOCUMENT AS A WHOLE"]
        for issue in report["general"]:
            lines.append(f"  - {issue['issue']}"
                         + (" (failed on review)" if issue["manual"] else ""))
    if report["confirm"]:
        lines += ["", "STILL TO CONFIRM BY EYE"]
        for entry in report["confirm"]:
            where = f"Page {entry['page']}: " if entry["page"] else ""
            lines.append(f"  - {where}{entry['label']}")
    if not n and not report["confirm"]:
        lines += ["", "No problems found."]
    lines += ["", f"Checks passed: {len(report['passed'])}. "
                  f"Not applicable: {len(report['not_applicable'])}."]
    return "\n".join(lines) + "\n"


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
