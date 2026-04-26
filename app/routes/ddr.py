"""
Due Diligence Report (DDR) routes.

Runs the DDR analysis as a background task so it doesn't block the
financial modeling pipeline. The frontend polls /api/ddr/status/{job_id}
to track progress and retrieve results.

Only triggers on PDF pitch deck uploads (not Excel financial models).
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

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from ..auth import CurrentUser, get_current_user
from ..database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ddr", tags=["ddr"])

# ── In-memory job store ──────────────────────────────────────────────────────
# Each job: {status, progress_pct, progress_msg, company_name, analysis, pdf_path, error, started_at, finished_at}
_DDR_JOBS: dict[str, dict] = {}
_DDR_LOCK = threading.Lock()

# Per-user cooldown tracking (username → last_start_time)
_DDR_COOLDOWNS: dict[str, float] = {}
_COOLDOWN_SECONDS = 90  # minimum seconds between analyses per user

# Directory for generated DDR PDFs
_DDR_OUTPUT_DIR = Path(tempfile.gettempdir()) / "volo_ddr_reports"
_DDR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _get_api_key() -> str:
    """Get Anthropic API key from environment."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set in environment / .env file")
    return key


def _run_ddr_background(job_id: str, pdf_bytes: bytes, filename: str):
    """Background worker that runs DDR analysis and PDF generation."""
    from ..engine.ddr_engine import extract_pdf, analyze
    from ..engine.ddr_report import generate_report_pdf

    def _update(status=None, progress_pct=None, progress_msg=None, **kwargs):
        with _DDR_LOCK:
            job = _DDR_JOBS[job_id]
            if status:
                job["status"] = status
            if progress_pct is not None:
                job["progress_pct"] = progress_pct
            if progress_msg:
                job["progress_msg"] = progress_msg
            job.update(kwargs)

    search_count = 0

    def _on_search_progress(n_searches):
        nonlocal search_count
        search_count += n_searches
        # Map search count to progress: 6-7 searches expected
        pct = min(30 + int((search_count / 7) * 50), 80)
        _update(progress_pct=pct, progress_msg=f"Web research in progress ({search_count} searches)...")

    # Background progress ticker — since the API call handles web searches
    # internally, we simulate progress so the user sees movement
    progress_ticker_active = True

    def _progress_ticker():
        """Slowly increment progress while waiting for the API call."""
        tick = 0
        while progress_ticker_active:
            time.sleep(10)
            if not progress_ticker_active:
                break
            tick += 1
            with _DDR_LOCK:
                job = _DDR_JOBS[job_id]
                if job["status"] != "analyzing":
                    break
                # Slowly move from 15 to 75 over ~4 minutes
                current = job["progress_pct"]
                new_pct = min(15 + tick * 3, 75)
                if new_pct > current:
                    messages = [
                        "AI analysis started — searching the web for verification data...",
                        "Researching company background and funding history...",
                        "Analyzing competitive landscape...",
                        "Verifying technology claims against independent sources...",
                        "Cross-referencing market data and valuations...",
                        "Checking litigation and IP status...",
                        "Compiling findings into structured report...",
                        "Finalizing analysis — almost done...",
                    ]
                    msg_idx = min(tick - 1, len(messages) - 1)
                    job["progress_pct"] = new_pct
                    job["progress_msg"] = messages[msg_idx]

    ticker_thread = threading.Thread(target=_progress_ticker, daemon=True)
    ticker_thread.start()

    try:
        # Wait for cooldown if another analysis ran recently
        with _DDR_LOCK:
            username = _DDR_JOBS[job_id].get("user", "")
        last_start = _DDR_COOLDOWNS.get(username, 0)
        wait_seconds = _COOLDOWN_SECONDS - (time.time() - last_start)
        if wait_seconds > 0:
            _update(status="queued", progress_pct=0,
                    progress_msg=f"Queued — waiting {int(wait_seconds)}s for API cooldown...")
            time.sleep(wait_seconds)

        # Record cooldown start for this user
        _DDR_COOLDOWNS[username] = time.time()

        _update(status="extracting", progress_pct=5,
                progress_msg="Extracting text from pitch deck...")

        # Write PDF bytes to temp file for extraction
        tmp_pdf = _DDR_OUTPUT_DIR / f"{job_id}_input.pdf"
        tmp_pdf.write_bytes(pdf_bytes)

        pitch_text = extract_pdf(str(tmp_pdf))
        if not pitch_text or len(pitch_text.strip()) < 100:
            _update(status="error", progress_pct=0,
                    error="PDF appears empty or unreadable. Cannot generate DDR.")
            return

        _update(status="analyzing", progress_pct=15,
                progress_msg="AI analysis started — searching the web for verification data...")

        api_key = _get_api_key()
        print(f"[DDR] Job {job_id}: Starting Claude analysis...", flush=True)
        analysis = analyze(api_key, pitch_text, on_progress=_on_search_progress)
        progress_ticker_active = False  # Stop the ticker
        print(f"[DDR] Job {job_id}: Analysis complete.", flush=True)

        if analysis.get("error"):
            _update(status="error", progress_pct=0,
                    error=f"Analysis failed: {analysis.get('error')}")
            return

        company_name = analysis.get("company_name", "Unknown")
        _update(status="generating_pdf", progress_pct=85,
                progress_msg="Generating PDF report...",
                company_name=company_name)

        # Generate PDF
        safe_name = "".join(c for c in company_name if c.isalnum() or c in " _-").strip()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_filename = f"DDR_{safe_name}_{timestamp}.pdf"
        pdf_path = str(_DDR_OUTPUT_DIR / pdf_filename)

        generate_report_pdf(analysis, pdf_path)

        # Save report to database for shared access
        try:
            with open(pdf_path, "rb") as f:
                pdf_data = f.read()
            db = get_db()
            try:
                with _DDR_LOCK:
                    generated_by = _DDR_JOBS[job_id].get("user", "unknown")
                db.execute(
                    "INSERT INTO ddr_reports (company_name, filename, pdf_data, analysis_json, "
                    "generated_by, file_size_bytes) VALUES (?,?,?,?,?,?)",
                    (company_name, pdf_filename, pdf_data, json.dumps(analysis),
                     generated_by, len(pdf_data)),
                )
                db.commit()
                print(f"[DDR] Report saved to database: {company_name}")
            finally:
                db.close()
        except Exception as save_err:
            print(f"[DDR] Warning: failed to save report to DB: {save_err}")

        _update(status="complete", progress_pct=100,
                progress_msg="Due diligence report ready.",
                company_name=company_name,
                analysis=analysis,
                pdf_path=pdf_path,
                pdf_filename=pdf_filename,
                finished_at=datetime.now().isoformat())

        # Clean up temp input PDF
        try:
            tmp_pdf.unlink()
        except OSError:
            pass

        logger.info(f"[DDR] Job {job_id} complete: {company_name}")

    except Exception as e:
        progress_ticker_active = False  # Stop the ticker
        import traceback
        traceback.print_exc()
        print(f"[DDR ERROR] Job {job_id} failed: {e}", flush=True)
        logger.exception(f"[DDR] Job {job_id} failed")
        _update(status="error", progress_pct=0,
                error=str(e))


# ── API Endpoints ────────────────────────────────────────────────────────────

@router.post("/start")
async def ddr_start(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Start a DDR background job. Accepts only PDF files.
    Returns a job_id for polling status.
    """
    if not file.filename:
        raise HTTPException(400, "No file provided")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext != "pdf":
        raise HTTPException(400, "DDR only accepts PDF pitch decks, not financial models.")

    # Check API key early
    try:
        _get_api_key()
    except ValueError as e:
        raise HTTPException(500, str(e))

    pdf_bytes = await file.read()
    if len(pdf_bytes) < 500:
        raise HTTPException(400, "File too small to be a valid pitch deck.")

    job_id = str(uuid.uuid4())[:12]

    with _DDR_LOCK:
        _DDR_JOBS[job_id] = {
            "status": "queued",
            "progress_pct": 0,
            "progress_msg": "Queued for processing...",
            "company_name": None,
            "analysis": None,
            "pdf_path": None,
            "pdf_filename": None,
            "error": None,
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "filename": file.filename,
            "user": user.username,
        }

    # Launch background thread
    t = threading.Thread(
        target=_run_ddr_background,
        args=(job_id, pdf_bytes, file.filename),
        daemon=True,
    )
    t.start()

    return {"job_id": job_id, "status": "queued"}


@router.get("/status/{job_id}")
async def ddr_status(job_id: str):
    """Poll DDR job status. Returns progress and results when complete."""
    with _DDR_LOCK:
        job = _DDR_JOBS.get(job_id)

    if not job:
        raise HTTPException(404, "DDR job not found")

    resp = {
        "job_id": job_id,
        "status": job["status"],
        "progress_pct": job["progress_pct"],
        "progress_msg": job["progress_msg"],
        "company_name": job["company_name"],
        "error": job["error"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "filename": job.get("filename"),
    }

    # Include analysis JSON when complete
    if job["status"] == "complete" and job["analysis"]:
        resp["analysis"] = job["analysis"]
        resp["pdf_filename"] = job["pdf_filename"]

    return resp


@router.get("/download/{job_id}")
async def ddr_download(job_id: str):
    """Download the generated DDR PDF."""
    with _DDR_LOCK:
        job = _DDR_JOBS.get(job_id)

    if not job:
        raise HTTPException(404, "DDR job not found")
    if job["status"] != "complete" or not job["pdf_path"]:
        raise HTTPException(400, "DDR report not ready yet")
    if not os.path.exists(job["pdf_path"]):
        raise HTTPException(404, "PDF file not found on disk")

    return FileResponse(
        job["pdf_path"],
        media_type="application/pdf",
        filename=job["pdf_filename"] or "DDR_Report.pdf",
    )


@router.get("/jobs")
async def ddr_list_jobs(user: CurrentUser = Depends(get_current_user)):
    """List all DDR jobs for the current user."""
    with _DDR_LOCK:
        jobs = []
        for jid, job in _DDR_JOBS.items():
            if job.get("user") == user.username:
                jobs.append({
                    "job_id": jid,
                    "status": job["status"],
                    "progress_pct": job["progress_pct"],
                    "company_name": job["company_name"],
                    "filename": job.get("filename"),
                    "started_at": job["started_at"],
                    "finished_at": job["finished_at"],
                })
    return {"jobs": sorted(jobs, key=lambda j: j["started_at"] or "", reverse=True)}


# ── Shared report history (persisted to DB) ──────────────────────────────────

@router.get("/reports")
async def ddr_list_reports(user: CurrentUser = Depends(get_current_user)):
    """List all DDR reports saved in the database (shared across all users)."""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, company_name, filename, generated_by, generated_at, file_size_bytes "
            "FROM ddr_reports ORDER BY generated_at DESC LIMIT 100"
        ).fetchall()
        return {"reports": [dict(r) for r in rows]}
    finally:
        db.close()


@router.delete("/reports/{report_id}")
async def ddr_delete_report(report_id: int, user: CurrentUser = Depends(get_current_user)):
    """Delete a saved DDR report. Anyone authenticated can delete since the
    DDR library is team-shared by design (matches the list endpoint's
    visibility model). Returns 404 if the row doesn't exist."""
    db = get_db()
    try:
        cur = db.execute("DELETE FROM ddr_reports WHERE id=?", (report_id,))
        db.commit()
        if (cur.rowcount or 0) == 0:
            raise HTTPException(404, "DDR report not found")
    finally:
        db.close()
    return {"ok": True, "deleted_id": report_id}


@router.get("/reports/{report_id}/download")
async def ddr_download_report(report_id: int, user: CurrentUser = Depends(get_current_user)):
    """Download a saved DDR report PDF from the database."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT filename, pdf_data FROM ddr_reports WHERE id=?", (report_id,)
        ).fetchone()
    finally:
        db.close()

    if not row:
        raise HTTPException(404, "Report not found")

    # Postgres BYTEA columns come back as `memoryview` via psycopg2, while
    # SQLite BLOB columns return `bytes`. Normalize to bytes so FastAPI's
    # Response handles Content-Length correctly on both backends.
    pdf_bytes = bytes(row["pdf_data"]) if row["pdf_data"] is not None else b""
    if not pdf_bytes:
        raise HTTPException(404, "PDF file is empty — re-generate the DDR.")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'},
    )
