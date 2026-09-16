# BoS Verifier

A small web app for the IQAC cell. Upload one or more scanned Board of Studies
PDFs and it checks each against the UGC checklist, reporting what passed, what
failed, and what still needs a human eye. The person checking can then pass or
fail each of those items page by page, and generate a plain report of the
failing pages and what is wrong with each.

## Running it

On Windows, **double-click `Start BoS Verifier.bat`**. It picks the Python
installation that has the packages, starts the app, and opens your browser at
it. Leave the black window open while you work; closing it stops the app.

From a terminal, either of these does the same:

```bash
python launcher.py
```

```bash
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000> and upload the BoS PDF. To check several
at once, pick several files (hold Ctrl or Shift); they are checked side by side,
up to three at a time, and a progress page links to each report as it finishes.

Tesseract OCR must be installed as well — a BoS is a stack of scans, so every
text-based check depends on it.

- Windows: install from <https://github.com/UB-Mannheim/tesseract/wiki>
  (the app looks in `C:\Program Files\Tesseract-OCR` by default)
- Linux: `apt install tesseract-ocr`
- macOS: `brew install tesseract`

If it lives somewhere else, set `TESSERACT_CMD` to the full path of the
executable before starting the app. The home page says so plainly if it
cannot find it.

Checking takes roughly five seconds a page — a 20-page BoS takes about three
minutes — because every page is OCR'd and then examined for seals, stamps and
signatures. The page shows progress while it works.

## What it checks

| # | Check | Notes |
|---|-------|-------|
| 1 | Letterhead on page 1 | The printed crest, rule or address footer — not just a typed heading |
| 2 | Round seal on every page | First page to last |
| 3 | Minutes signed off where they end | Principal and HOD, stamp and seal |
| 4 | Agenda items covered in the minutes | Every item listed on the agenda must be minuted, and what is recorded under each must be that item |
| 5 | Attendance sheet on letterhead | |
| 6 | Attendance matches who was present | There must be a page recording who was present and absent. A member marked absent must not have signed the attendance sheet; a member listed as present must have |
| 7 | Annexure-1 percentage modification | "Nil" is fine; needs Principal and HOD sign and seal |
| 8 | Annexure-2 covers Annexure-1 | Every course listed as modified must have its syllabus attached |
| 9 | Odd semester: result analysis | With graphs and the round seal |
| 10 | Even semester: stakeholder feedback | Students, alumni, teachers and employers |
| 11 | Action Taken Report | Signed and sealed |
| 12 | Even semester: feedback graphs signed | HOD and Principal, with seal |

Odd or even semester comes from the meeting date: a meeting held July–December
reviews the odd semester, January–June the even one. Rules that do not apply
to the semester in hand are marked N/A rather than failed.

## Pass, confirm, fail

Every check lands in one of four states, and the middle one matters:

- **Pass** — met, on evidence solid enough to rely on.
- **Confirm** — the evidence is there but weaker than a clean pass: a faded
  seal, a stamp whose text did not survive the scan, a chart page too dark to
  read. Worth a glance; it is not a finding against the document.
- **Fail** — not met.
- **N/A** — does not apply to this semester.

A scan of a rubber stamp is not a machine-readable record. A checker that
pretends otherwise either cries wolf on good documents or waves bad ones
through, so where the evidence is genuinely ambiguous the app says so instead
of guessing. The overall verdict is "not passable" if anything failed,
"passable, with points to confirm" if anything needs an eye, otherwise
"passable".

## The report

The report opens with the verdict and a **page-by-page table**: what each page
is, and whether it carries the seal, the letterhead, and the HOD's and
Principal's endorsement. "View" opens any page at a readable size.

Then comes the **checklist**. Every check marked Confirm lists what to look at,
one item per page, each with **Pass** and **Fail** buttons and a link to the
page. The check's result follows from those decisions: any item failed fails
it, all items passed passes it. Clicking a chosen button again clears it. The
verdict and tally at the top update as you go. The attendance check also shows
every member on the roll, how they were recorded, and what the sheet shows.

Below the checklist, **Generate report** produces a point-by-point list of the
failing pages and what is wrong with each, including anything failed by hand,
followed by anything still to confirm. It can be copied as text, downloaded as
a .txt file, or printed / saved as a PDF. Decisions are kept while the app runs;
generate again after changing them.

## How the hard parts work

**Seals.** The round college seal is the thing most rules hang on, and it is
also the hardest to find: across the sample documents it ranges from a crisp
violet impression to a barely visible pink ghost, and on chart pages it is
half swallowed by a dark block of toner. Colour is no help — on some scans the
whole page is colour-cast and the stamp ink is nearly black — so the detector
works on structure instead:

- the rim's image gradient must point along the radius almost all the way
  round, which is what separates a stamped circle from a signature, a run of
  text or a patch of scanner shading;
- those aligned angles must form one continuous arc, not a lucky scatter;
- there must be ink on two concentric rims, which rejects the outline of a
  pie chart;
- and the rim must sit in whitespace.

Ring coverage on its own passes almost any inked region of a page — measured
against pages with the seal painted out, it fired on 33 out of 33 — so the
alignment test is what makes the check mean anything.

**Stamps and signatures.** The Principal's stamp usually will not OCR: it is
struck through by a signature, and across the two sample documents the literal
word "Principal" survives on only two of the five pages that carry it. So a
stamp is located as a cluster of the college address block low on the page —
every stamp repeats the college name and PIN code — using OCR word positions
where the text survived and colour blocks where it did not. Where a role
cannot be read, the app says which weaker cue it relied on rather than
claiming a clean pass. Signatures are found as ink the OCR could *not* read.

**Agenda against minutes.** The agenda is listed at the front, in one of two
layouts (numbered items under a heading, or one "Agenda N" marker per item),
and worked through later under the same numbers. OCR damages the word itself
often — "Agenda no 3" comes back as "Acenda no 3", and on one sample page the
label is lost where the scan clipped the margin — so an item is looked for
twice: by its number, and by its subject wording anywhere in the minutes.
Wording is compared with light stemming, and procedural items (the welcome,
the vote of thanks, "any other business") are exempt from the comparison
because minutes always record those in words of their own. Only an item that
is not minuted at all fails; a paraphrase is flagged to read, not failed.

**Attendance against the roll.** The page recording who was present is found
near the front: it says "present", "absent" or "attendees", and names more
members than any other such page. Each member is then found on the attendance
sheet by name - surname first, with first names and initials settling which of
several same-surname members it is (the sample sheet has three Patels) - and
their row is examined for handwriting, since a signature is ink the OCR could
not read. Three things had to be got right:

- the ruled line a member signs on survives OCR exactly as a signature does, so
  rules are removed first, and found on the untouched page, because masking
  the recognised words cuts every rule a name sits on;
- sheets come in two layouts - a signature line per name, or a table row - and
  the signing space is above the line in one and the row itself in the other.
  Using the wrong shape put the alumnus's tall signature into the absent
  industry representative's empty row on the sample sheet;
- a slightly skewed scan breaks a one-pixel table rule into pieces too short to
  recognise unless the line is thickened first.

On the two sample documents this separates cleanly: of 28 members across four
sheets, every signed row reads at least 2.1% ink and every unsigned row at most
0.8%. A member present but clearly unsigned, or absent but clearly signed,
fails; a reading in between, or a present member not found on the sheet, is
listed to confirm. On the Statistics BoS it finds two members listed as
attendees who did not sign - which agrees with the minutes' own count of 13
of 15 members present.

**Sideways pages.** Feedback charts are often bound sideways. Tesseract's own
orientation detector is unreliable on them — on the sample pages it reported
confidences as low as 0.12 and guessed the wrong script — so orientation is
chosen by OCR'ing each quarter-turn and keeping whichever reads best.

## Known limits

- **Graph detection is deliberately weak.** A grey bar chart photographed off
  paper does not separate from prose on shape alone. Two approaches were tried
  and abandoned (counting solid blocks catches stamps and table cells;
  requiring bars to share an edge catches every left-aligned paragraph), so a
  result or feedback page with no detected graph is reported for confirmation
  rather than failed.
- **A pie chart can read as a seal.** It is a genuine circle, and on one
  sample page the detector accepts it. It only matters on a page that has a
  pie chart and no seal.
- **One page of a correct document may be flagged for confirmation.** Seals
  stamped half over a dark chart score below a confident pass by design.
- Jobs and the decisions made on them live in memory, so closing the app loses
  past reports. Download the text report or the JSON if you need to keep one.
- **The attendance thresholds come from two documents.** They separate the
  sample sheets with a clear margin, but a sheet in a new layout or a very faint
  scan may need them adjusting (SIGNED_INK and UNSIGNED_INK in
  bosverify/attendance.py).

## Layout

```
app.py                 Flask app: uploads, parallel checking, report, decisions
bosverify/
  document.py          Runs every detector over a PDF, once per page
  rules.py             The twelve checks
  seals.py             Round seal detection
  authority.py         HOD / Principal stamps and signatures
  letterhead.py        Printed letterhead vs a typed heading
  structure.py         Page kinds, semester, courses, charts
  agenda.py            The agenda, and whether the minutes cover it
  attendance.py        The present / absent roll versus signatures
  ocr.py               OCR with orientation recovery
  marks.py             Coloured-ink blocks
  imaging.py           Shared image helpers
  pdfio.py             Page rendering
launcher.py            Starts the app and opens the browser
Start BoS Verifier.bat Double-click launcher for Windows
templates/             Upload, progress, batch, report and generated-report pages
static/                Styling
```
