"""
Technical / Deep-Tech Due Diligence Report (Tech DDR) routes.

A parallel track to routes/ddr.py for science-heavy deals. Differences:
  * accepts MULTIPLE files (research papers + optional pitch deck), not one,
  * accepts a free-text `innovation_hint` (the analyst's hypothesis),
  * runs the Opus multi-pass engine (tech_ddr_engine),
  * persists to the tech_ddr_reports table.

The standard DDR routes are left completely untouched.
"""

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from ..auth import CurrentUser, get_current_user
from ..database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tech-ddr", tags=["tech-ddr"])

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
_COOLDOWNS: dict[str, float] = {}
_COOLDOWN_SECONDS = 90

# Hard wall-clock backstop: a job MUST reach a terminal state within this many
# seconds. The engine targets a shorter soft deadline and degrades gracefully,
# so this rarely fires — it just guarantees the UI never waits forever.
_HARD_LIMIT_SECONDS = 1200  # 20 minutes

_OUTPUT_DIR = Path(tempfile.gettempdir()) / "volo_tech_ddr_reports"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_MAX_FILES = 8


def _get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set in environment / .env file")
    return key


def _run_background(job_id: str, files: list[tuple[str, bytes]], innovation_hint: str):
    """Background worker: extract all docs in full → 3-pass analyze → PDF → save."""
    from ..engine.tech_ddr_engine import extract_pdf_full, analyze_tech
    from ..engine.tech_ddr_report import generate_tech_report_pdf

    def _set(**kwargs):
        """Update progress / intermediate fields — no-op once the job is final."""
        with _LOCK:
            job = _JOBS.get(job_id)
            if not job or job.get("_final"):
                return
            job.update(kwargs)

    def _progress(pct, msg):
        _set(progress_pct=int(pct), progress_msg=str(msg))

    def _finalize(**kwargs):
        """Set the terminal state exactly once (first writer wins — worker or watchdog)."""
        with _LOCK:
            job = _JOBS.get(job_id)
            if not job or job.get("_final"):
                return
            job.update(kwargs)
            job["_final"] = True

    def _is_final() -> bool:
        with _LOCK:
            return bool(_JOBS.get(job_id, {}).get("_final"))

    # Hard backstop: guarantee a terminal state within the wall-clock limit even
    # if analysis stalls. The engine degrades gracefully well before this fires.
    def _watchdog():
        time.sleep(_HARD_LIMIT_SECONDS)
        _finalize(status="error", progress_pct=0, progress_msg="Timed out.",
                  error=f"Timed out after {_HARD_LIMIT_SECONDS // 60} minutes. "
                        f"Try fewer or smaller documents.",
                  finished_at=datetime.now().isoformat())
    threading.Thread(target=_watchdog, daemon=True).start()

    try:
        # Cooldown so back-to-back runs don't hammer the API.
        with _LOCK:
            username = _JOBS[job_id].get("user", "")
        wait = _COOLDOWN_SECONDS - (time.time() - _COOLDOWNS.get(username, 0))
        if wait > 0:
            _set(status="queued", progress_pct=0,
                 progress_msg=f"Queued — waiting {int(wait)}s for API cooldown...")
            time.sleep(wait)
        _COOLDOWNS[username] = time.time()

        _set(status="extracting", progress_pct=5,
             progress_msg="Extracting full text from documents...")

        docs = []
        for fname, data in files:
            tmp = _OUTPUT_DIR / f"{job_id}_{uuid.uuid4().hex[:6]}.pdf"
            tmp.write_bytes(data)
            try:
                text = extract_pdf_full(str(tmp))
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            docs.append({"filename": fname, "text": text})

        readable = [d for d in docs if (d["text"] or "").strip()]
        if not readable:
            _finalize(status="error", progress_pct=0,
                      error="Could not extract readable text from any uploaded PDF.",
                      finished_at=datetime.now().isoformat())
            return

        _set(status="analyzing", progress_pct=15,
             progress_msg="Reading documents in full...")

        api_key = _get_api_key()
        print(f"[TechDDR] Job {job_id}: starting Opus multi-pass analysis "
              f"({len(readable)} docs)...", flush=True)
        analysis = analyze_tech(api_key, readable, innovation_hint=innovation_hint,
                                progress=_progress)

        # If the watchdog already finalized this job (timeout), stop silently.
        if _is_final():
            return

        if analysis.get("error"):
            _finalize(status="error", progress_pct=0,
                      error=f"Analysis failed: {analysis.get('error')}",
                      finished_at=datetime.now().isoformat())
            return

        company_name = analysis.get("company_name") or "Unknown Subject"
        _set(status="generating_pdf", progress_pct=92,
             progress_msg="Generating PDF report...", company_name=company_name)

        safe = "".join(c for c in company_name if c.isalnum() or c in " _-").strip() or "report"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_filename = f"TechDDR_{safe}_{ts}.pdf"
        pdf_path = str(_OUTPUT_DIR / pdf_filename)
        generate_tech_report_pdf(analysis, pdf_path)

        # Persist for the shared team library.
        try:
            with open(pdf_path, "rb") as f:
                pdf_data = f.read()
            db = get_db()
            try:
                with _LOCK:
                    generated_by = _JOBS[job_id].get("user", "unknown")
                source_docs = ", ".join(fname for fname, _ in files)
                db.execute(
                    "INSERT INTO tech_ddr_reports (company_name, filename, pdf_data, "
                    "analysis_json, innovation_hint, source_docs, generated_by, "
                    "file_size_bytes) VALUES (?,?,?,?,?,?,?,?)",
                    (company_name, pdf_filename, pdf_data, json.dumps(analysis),
                     innovation_hint, source_docs, generated_by, len(pdf_data)),
                )
                db.commit()
            finally:
                db.close()
        except Exception as save_err:
            print(f"[TechDDR] Warning: failed to save report to DB: {save_err}", flush=True)

        is_partial = bool(analysis.get("partial"))
        _finalize(status="complete", progress_pct=100,
                  progress_msg=("Partial report saved — synthesis did not finish."
                                if is_partial else "Technical due diligence report ready."),
                  company_name=company_name, analysis=analysis, pdf_path=pdf_path,
                  pdf_filename=pdf_filename, partial=is_partial,
                  partial_reason=analysis.get("partial_reason", ""),
                  finished_at=datetime.now().isoformat())
        logger.info(f"[TechDDR] Job {job_id} {'complete (PARTIAL)' if is_partial else 'complete'}: {company_name}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[TechDDR ERROR] Job {job_id} failed: {e}", flush=True)
        logger.exception(f"[TechDDR] Job {job_id} failed")
        _finalize(status="error", progress_pct=0, error=str(e),
                  finished_at=datetime.now().isoformat())


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/start")
async def tech_ddr_start(
    files: list[UploadFile] = File(...),
    innovation_hint: str = Form(""),
    user: CurrentUser = Depends(get_current_user),
):
    """Start a Technical DDR job. Accepts 1–8 PDF files + an optional hypothesis."""
    # Diagnostic: prints the instant the request reaches the handler (after auth +
    # multipart parse). If you click Generate and DON'T see this line in the logs,
    # the request never reached the handler (client-side throw, or blocked before routing).
    print(f"[TechDDR] /start HIT — user={getattr(user, 'username', '?')} "
          f"files={len(files) if files else 0}", flush=True)
    if not files:
        raise HTTPException(400, "No files provided")
    if len(files) > _MAX_FILES:
        raise HTTPException(400, f"Too many files (max {_MAX_FILES}).")

    try:
        _get_api_key()
    except ValueError as e:
        raise HTTPException(500, str(e))

    collected: list[tuple[str, bytes]] = []
    for f in files:
        if not f.filename:
            continue
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext != "pdf":
            raise HTTPException(400, f"'{f.filename}' is not a PDF. Technical DDR accepts PDF papers/decks.")
        data = await f.read()
        if len(data) < 500:
            raise HTTPException(400, f"'{f.filename}' is too small to be a valid document.")
        collected.append((f.filename, data))

    if not collected:
        raise HTTPException(400, "No valid PDF files provided.")

    job_id = str(uuid.uuid4())[:12]
    with _LOCK:
        _JOBS[job_id] = {
            "status": "queued", "progress_pct": 0,
            "progress_msg": "Queued for processing...",
            "company_name": None, "analysis": None, "pdf_path": None,
            "pdf_filename": None, "error": None,
            "started_at": datetime.now().isoformat(), "finished_at": None,
            "filename": ", ".join(fn for fn, _ in collected),
            "innovation_hint": innovation_hint, "user": user.username,
        }

    threading.Thread(
        target=_run_background, args=(job_id, collected, innovation_hint), daemon=True,
    ).start()
    return {"job_id": job_id, "status": "queued"}


@router.get("/status/{job_id}")
async def tech_ddr_status(job_id: str):
    with _LOCK:
        job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Tech DDR job not found")
    resp = {
        "job_id": job_id, "status": job["status"],
        "progress_pct": job["progress_pct"], "progress_msg": job["progress_msg"],
        "company_name": job["company_name"], "error": job["error"],
        "started_at": job["started_at"], "finished_at": job["finished_at"],
        "filename": job.get("filename"),
        "partial": job.get("partial", False),
        "partial_reason": job.get("partial_reason", ""),
    }
    if job["status"] == "complete" and job["analysis"]:
        resp["analysis"] = job["analysis"]
        resp["pdf_filename"] = job["pdf_filename"]
    return resp


@router.get("/download/{job_id}")
async def tech_ddr_download(job_id: str):
    with _LOCK:
        job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Tech DDR job not found")
    if job["status"] != "complete" or not job["pdf_path"]:
        raise HTTPException(400, "Report not ready yet")
    if not os.path.exists(job["pdf_path"]):
        raise HTTPException(404, "PDF file not found on disk")
    return FileResponse(job["pdf_path"], media_type="application/pdf",
                        filename=job["pdf_filename"] or "TechDDR_Report.pdf")


@router.get("/jobs")
async def tech_ddr_list_jobs(user: CurrentUser = Depends(get_current_user)):
    with _LOCK:
        jobs = [{
            "job_id": jid, "status": j["status"], "progress_pct": j["progress_pct"],
            "company_name": j["company_name"], "filename": j.get("filename"),
            "started_at": j["started_at"], "finished_at": j["finished_at"],
        } for jid, j in _JOBS.items() if j.get("user") == user.username]
    return {"jobs": sorted(jobs, key=lambda j: j["started_at"] or "", reverse=True)}


@router.get("/reports")
async def tech_ddr_list_reports(user: CurrentUser = Depends(get_current_user)):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, company_name, filename, custom_title, source_docs, "
            "generated_by, generated_at, file_size_bytes FROM tech_ddr_reports "
            "ORDER BY generated_at DESC LIMIT 100"
        ).fetchall()
        return {"reports": [dict(r) for r in rows]}
    finally:
        db.close()


@router.delete("/reports/{report_id}")
async def tech_ddr_delete_report(report_id: int, user: CurrentUser = Depends(get_current_user)):
    db = get_db()
    try:
        cur = db.execute("DELETE FROM tech_ddr_reports WHERE id=?", (report_id,))
        db.commit()
        if (cur.rowcount or 0) == 0:
            raise HTTPException(404, "Tech DDR report not found")
    finally:
        db.close()
    return {"ok": True, "deleted_id": report_id}


class _RenameRequest(BaseModel):
    title: str = ""


@router.patch("/reports/{report_id}/title")
async def tech_ddr_rename_report(report_id: int, body: _RenameRequest,
                                 user: CurrentUser = Depends(get_current_user)):
    new_title = (body.title or "").strip()[:200]
    db = get_db()
    try:
        cur = db.execute("UPDATE tech_ddr_reports SET custom_title=? WHERE id=?",
                         (new_title, report_id))
        db.commit()
        if (cur.rowcount or 0) == 0:
            raise HTTPException(404, "Tech DDR report not found")
    finally:
        db.close()
    return {"ok": True, "id": report_id, "custom_title": new_title}


@router.get("/reports/{report_id}/download")
async def tech_ddr_download_report(report_id: int, user: CurrentUser = Depends(get_current_user)):
    db = get_db()
    try:
        row = db.execute(
            "SELECT filename, pdf_data FROM tech_ddr_reports WHERE id=?", (report_id,)
        ).fetchone()
    finally:
        db.close()
    if not row:
        raise HTTPException(404, "Report not found")
    pdf_bytes = bytes(row["pdf_data"]) if row["pdf_data"] is not None else b""
    if not pdf_bytes:
        raise HTTPException(404, "PDF file is empty — re-generate the report.")
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'})
