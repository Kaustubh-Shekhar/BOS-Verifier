"""BoS Verifier - a small web app for the IQAC cell.

Upload one or more scanned Board of Studies PDFs; the app runs the UGC
checklist over each and reports what passed, what failed, and what a person
still needs to look at.  Items needing a look can be passed or failed on the
report page, and a plain summary of the problems generated from the result.

Run it with:  python app.py     then open http://127.0.0.1:5000
"""
import os
import tempfile
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# Tesseract parallelises internally.  With several documents being read at
# once that oversubscribes the CPU and every job slows down, so each OCR call
# is held to one thread and the parallelism comes from running jobs side by
# side instead.  Set before pytesseract starts any process.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, url_for)
from werkzeug.utils import secure_filename

from bosverify import rules
from bosverify.document import analyse
from bosverify.ocr import tesseract_available

MAX_UPLOAD_MB = 200
# How many documents are checked at the same time.  Each job is mostly OCR,
# which is one CPU core apiece with the thread limit above.
WORKERS = max(1, min(4, (os.cpu_count() or 2) - 1))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# Jobs live in memory only: this is a tool one person runs on their own
# machine, and nothing here should outlive it.
JOBS = {}
BATCHES = {}
LOCK = threading.Lock()
POOL = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="bos")


class Job:
    def __init__(self, filename):
        self.id = uuid.uuid4().hex[:12]
        self.filename = filename
        self.state = "queued"
        self.done = 0
        self.total = 0
        self.error = None
        self.document = None
        self.checks = []
        # The person's pass/fail calls on items in review:
        # {check key: {item id: "pass" | "fail"}}
        self.decisions = {}
        self.started = datetime.now()

    @property
    def verdict(self):
        return rules.verdict(self.checks, self.decisions) if self.checks else None


def _run_job(job, path):
    try:
        job.state = "running"

        def progress(done, total):
            job.done, job.total = done, total

        document = analyse(path, name=job.filename, progress=progress)
        job.document = document
        job.checks = rules.run(document)
        job.state = "done"
    except Exception as exc:  # surfaced to the user rather than swallowed
        job.error = f"{type(exc).__name__}: {exc}"
        job.state = "failed"
        traceback.print_exc()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _home(error=None, status=200):
    return render_template("index.html", tesseract=tesseract_available(),
                           max_mb=MAX_UPLOAD_MB, workers=WORKERS,
                           error=error), status


def _job_or_404(job_id):
    job = JOBS.get(job_id)
    if job is None:
        abort(404)
    return job


@app.route("/")
def index():
    return _home()


@app.route("/upload", methods=["POST"])
def upload():
    files = [f for f in request.files.getlist("bos") if f and f.filename]
    if not files:
        return _home("Choose at least one PDF to check.", 400)
    not_pdf = [f.filename for f in files if not f.filename.lower().endswith(".pdf")]
    if not_pdf:
        return _home("These are not PDFs: " + ", ".join(not_pdf)
                     + ". A BoS is a scanned PDF; please upload the PDF itself.", 400)

    jobs = []
    for f in files:
        handle, path = tempfile.mkstemp(suffix=".pdf")
        os.close(handle)
        f.save(path)
        job = Job(secure_filename(f.filename) or "document.pdf")
        with LOCK:
            JOBS[job.id] = job
        POOL.submit(_run_job, job, path)
        jobs.append(job)

    if len(jobs) == 1:
        return redirect(url_for("progress_page", job_id=jobs[0].id))
    batch_id = uuid.uuid4().hex[:12]
    with LOCK:
        BATCHES[batch_id] = [j.id for j in jobs]
    return redirect(url_for("batch_page", batch_id=batch_id))


# --- progress --------------------------------------------------------------

def _job_state(job):
    return dict(id=job.id, file=job.filename, state=job.state, done=job.done,
                total=job.total, error=job.error, verdict=job.verdict,
                report=url_for("report", job_id=job.id))


@app.route("/checking/<job_id>")
def progress_page(job_id):
    job = _job_or_404(job_id)
    if job.state == "done":
        return redirect(url_for("report", job_id=job_id))
    return render_template("checking.html", job=job)


@app.route("/status/<job_id>")
def status(job_id):
    return jsonify(_job_state(_job_or_404(job_id)))


@app.route("/batch/<batch_id>")
def batch_page(batch_id):
    ids = BATCHES.get(batch_id)
    if ids is None:
        abort(404)
    return render_template("batch.html", batch_id=batch_id,
                           jobs=[JOBS[i] for i in ids], workers=WORKERS)


@app.route("/batch/<batch_id>/status")
def batch_status(batch_id):
    ids = BATCHES.get(batch_id)
    if ids is None:
        abort(404)
    return jsonify(jobs=[_job_state(JOBS[i]) for i in ids])


# --- the report ------------------------------------------------------------

def _outcome(job):
    return dict(
        verdict=job.verdict,
        counts=rules.tally(job.checks, job.decisions),
        statuses={c.key: rules.status_after(c, job.decisions) for c in job.checks},
    )


@app.route("/report/<job_id>")
def report(job_id):
    job = _job_or_404(job_id)
    if job.state == "failed":
        return render_template("failed.html", job=job), 500
    if job.state != "done":
        return redirect(url_for("progress_page", job_id=job_id))
    return render_template("report.html", job=job, doc=job.document,
                           checks=job.checks, outcome=_outcome(job), rules=rules)


@app.route("/report/<job_id>/decide", methods=["POST"])
def decide(job_id):
    """Record a person's pass/fail call on one item, or clear it."""
    job = _job_or_404(job_id)
    if job.state != "done":
        return jsonify(error="The report is not ready."), 409
    data = request.get_json(silent=True) or {}
    check = next((c for c in job.checks if c.key == data.get("check")), None)
    if check is None or check.status != rules.REVIEW:
        return jsonify(error="That check has nothing to decide."), 400
    item_id = data.get("item")
    if not any(i["id"] == item_id for i in check.items):
        return jsonify(error="Unknown item."), 400
    decision = data.get("decision")
    if decision not in ("pass", "fail", None):
        return jsonify(error="A decision is pass, fail or empty."), 400

    with LOCK:
        marks = job.decisions.setdefault(check.key, {})
        if decision is None:
            marks.pop(item_id, None)
        else:
            marks[item_id] = decision
    out = _outcome(job)
    out["check"] = dict(key=check.key, status=out["statuses"][check.key])
    return jsonify(out)


@app.route("/report/<job_id>/summary")
def summary(job_id):
    """The generated report of problems, as an HTML fragment for the page."""
    job = _job_or_404(job_id)
    if job.state != "done":
        abort(409)
    report_data = rules.summary(job.document, job.checks, job.decisions)
    return render_template("summary.html", job=job, doc=job.document,
                           report=report_data, rules=rules,
                           generated=datetime.now())


@app.route("/report/<job_id>/summary.txt")
def summary_text(job_id):
    job = _job_or_404(job_id)
    if job.state != "done":
        abort(409)
    report_data = rules.summary(job.document, job.checks, job.decisions)
    text = rules.summary_text(job.document, report_data, job.filename)
    stem = os.path.splitext(job.filename)[0]
    return Response(text, mimetype="text/plain; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{stem}-summary.txt"'})


@app.route("/report/<job_id>/page/<int:number>.jpg")
def page_image(job_id, number):
    """A page at readable size, for confirming an item by eye."""
    job = _job_or_404(job_id)
    if job.state != "done" or not 1 <= number <= len(job.document.pages):
        abort(404)
    image = job.document.pages[number - 1].image
    if not image:
        abort(404)
    return Response(image, mimetype="image/jpeg")


@app.route("/report/<job_id>.json")
def report_json(job_id):
    job = _job_or_404(job_id)
    if job.state != "done":
        return jsonify(state=job.state), 409
    doc = job.document
    outcome = _outcome(job)
    return jsonify(
        file=job.filename,
        meeting_date=doc.meeting_date.isoformat() if doc.meeting_date else None,
        semester=doc.semester,
        pages=len(doc.pages),
        verdict=outcome["verdict"],
        decisions=job.decisions,
        checks=[dict(c.as_dict(), status_after_review=outcome["statuses"][c.key])
                for c in job.checks],
        roll=doc.roll,
        page_detail=[dict(page=p.number, kinds=sorted(p.kinds),
                          seal=p.seal_state, letterhead=p.letterhead["found"],
                          rotation=p.rotation,
                          hod=p.authority["hod"]["found"],
                          principal=p.authority["principal"]["found"])
                     for p in doc.pages],
    )


@app.errorhandler(413)
def too_large(_):
    return _home(f"Those files add up to more than {MAX_UPLOAD_MB} MB. "
                 "Upload fewer at a time.", 413)


if __name__ == "__main__":
    if not tesseract_available():
        print("WARNING: Tesseract OCR was not found. Install it, or set "
              "TESSERACT_CMD to its path. Every text-based check needs it.")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
