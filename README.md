# BoS Verifier

A small web app for the IQAC cell. Upload a scanned Board of Studies PDF and
it checks it against the UGC checklist, reporting what passed, what failed,
and what still needs a human eye.

## Running it

```bash
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000> and upload the BoS PDF.

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
| 4 | Attendance sheet on letterhead | |
| 5 | Annexure-1 percentage modification | "Nil" is fine; needs Principal and HOD sign and seal |
| 6 | Annexure-2 covers Annexure-1 | Every course listed as modified must have its syllabus attached |
| 7 | Odd semester: result analysis | With graphs and the round seal |
| 8 | Even semester: stakeholder feedback | Students, alumni, teachers and employers |
| 9 | Action Taken Report | Signed and sealed |
| 10 | Even semester: feedback graphs signed | HOD and Principal, with seal |

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
- Jobs live in memory, so a restart loses past reports. Download the JSON if
  you need to keep one.

## Layout

```
app.py                 Flask app: upload, progress, report
bosverify/
  document.py          Runs every detector over a PDF, once per page
  rules.py             The ten checks
  seals.py             Round seal detection
  authority.py         HOD / Principal stamps and signatures
  letterhead.py        Printed letterhead vs a typed heading
  structure.py         Page kinds, semester, courses, charts
  ocr.py               OCR with orientation recovery
  marks.py             Coloured-ink blocks
  imaging.py           Shared image helpers
  pdfio.py             Page rendering
templates/, static/    The web pages
```
