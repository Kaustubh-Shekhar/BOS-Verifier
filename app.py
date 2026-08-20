"""BoS Verifier - a small web app for the IQAC cell.

Upload the scanned Board of Studies PDF; the app runs the UGC checklist over
it and reports what passed, what failed, and what a person still needs to
look at.

Run it with:  python app.py     then open http://127.0.0.1:5000
"""
import os
import tempfile
import threading
import traceback
import uuid
from datetime import datetime

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   url_for)
from werkzeug.utils import secure_filename

from bosverify import rules
from bosverify.document import analyse
from bosverify.ocr import tesseract_available

MAX_UPLOAD_MB = 60

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# Analysis takes a minute or two per document, so it runs on a worker thread
# and the page polls for progress.  Jobs live in memory only: this is a tool
# one person runs on their own machine, and nothing here should outlive it.
JOBS = {}
JOBS_LOCK = threading.Lock()


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
        self.verdict = None
        self.started = datetime.now()


def _run_job(job, path):
    try:
        job.state = "running"

        def progress(done, total):
            job.done, job.total = done, total

        document = analyse(path, name=job.filename, progress=progress)
        job.document = document
        job.checks = rules.run(document)
        job.verdict = rules.verdict(job.checks)
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


@app.route("/")
def index():
    return render_template("index.html", tesseract=tesseract_available(),
                           max_mb=MAX_UPLOAD_MB)


@app.route("/upload", methods=["POST"])
def upload():
    upload = request.files.get("bos")
    if not upload or not upload.filename:
        return render_template("index.html", tesseract=tesseract_available(),
                               max_mb=MAX_UPLOAD_MB,
                               error="Choose a PDF to check."), 400
    if not upload.filename.lower().endswith(".pdf"):
        return render_template("index.html", tesseract=tesseract_available(),
                               max_mb=MAX_UPLOAD_MB,
                               error="That is not a PDF. A BoS is a scanned "
                                     "PDF; please upload the PDF itself."), 400

    handle, path = tempfile.mkstemp(suffix=".pdf")
    os.close(handle)
    upload.save(path)

    job = Job(secure_filename(upload.filename))
    with JOBS_LOCK:
        JOBS[job.id] = job
    threading.Thread(target=_run_job, args=(job, path), daemon=True).start()
    return redirect(url_for("progress_page", job_id=job.id))


@app.route("/checking/<job_id>")
def progress_page(job_id):
    job = JOBS.get(job_id) or abort(404)
    if job.state == "done":
        return redirect(url_for("report", job_id=job_id))
    return render_template("checking.html", job=job)


@app.route("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id) or abort(404)
    return jsonify(state=job.state, done=job.done, total=job.total,
                   error=job.error)


@app.route("/report/<job_id>")
def report(job_id):
    job = JOBS.get(job_id) or abort(404)
    if job.state == "failed":
        return render_template("failed.html", job=job), 500
    if job.state != "done":
        return redirect(url_for("progress_page", job_id=job_id))

    doc = job.document
    counts = {level: sum(1 for c in job.checks if c.status == level)
              for level in (rules.PASS, rules.REVIEW, rules.FAIL, rules.NA)}
    return render_template("report.html", job=job, doc=doc, checks=job.checks,
                           verdict=job.verdict, counts=counts, rules=rules)


@app.route("/report/<job_id>.json")
def report_json(job_id):
    job = JOBS.get(job_id) or abort(404)
    if job.state != "done":
        return jsonify(state=job.state), 409
    doc = job.document
    return jsonify(
        file=job.filename,
        meeting_date=doc.meeting_date.isoformat() if doc.meeting_date else None,
        semester=doc.semester,
        pages=len(doc.pages),
        verdict=job.verdict,
        checks=[c.as_dict() for c in job.checks],
        page_detail=[dict(page=p.number, kinds=sorted(p.kinds),
                          seal=p.seal_state, letterhead=p.letterhead["found"],
                          rotation=p.rotation,
                          hod=p.authority["hod"]["found"],
                          principal=p.authority["principal"]["found"])
                     for p in doc.pages],
    )


@app.errorhandler(413)
def too_large(_):
    return render_template("index.html", tesseract=tesseract_available(),
                           max_mb=MAX_UPLOAD_MB,
                           error=f"That file is larger than {MAX_UPLOAD_MB} MB."), 413


if __name__ == "__main__":
    if not tesseract_available():
        print("WARNING: Tesseract OCR was not found. Install it, or set "
              "TESSERACT_CMD to its path. Every text-based check needs it.")
    app.run(host="127.0.0.1", port=5000, debug=False)
