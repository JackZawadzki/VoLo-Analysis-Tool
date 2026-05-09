"""
Year-by-year financial-model snapshots for portfolio companies.

For one company, this:
  1. Walks the company's linked Drive folders (current + diligence + board_pack)
     and collects every Excel file that looks like a financial / operating model.
  2. For each model file, parses a snapshot year from the filename or from
     Drive's modifiedTime — this is the year the model was the company's
     current operating plan.
  3. Downloads each file to a tempfile and runs the existing diligence-time
     financial extractor (`app.engine.financial_pipeline.run_pipeline`) over
     it. Same extractor, same line-item schema as deal-flow / IC.
  4. Persists one row per model into pr_financial_snapshots, storing the
     full {metric: {fiscal_year: value}} pivot plus enough provenance to
     audit which file produced which numbers.

Reading these back row-by-row lets the UI assemble a plan-vs-actuals matrix:
the 2024 model's projection for FY2026 sits next to the 2026 model's actual
FY2026 historical column, and the variance is the partner-relevant signal.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── File classification ──────────────────────────────────────────────────────
# Reuse the same patterns the traction scanner uses — these match the names
# operators actually use ("Operating Model", "Forecast", "Financial Model",
# "FY24 P&L", etc.). We keep this list narrow so we don't accidentally extract
# from cap tables, valuation summaries, or comp pulls that happen to be xlsx.
MODEL_NAME_PATTERNS = re.compile(
    r"financial.?model|operating.?model|projection|forecast|"
    r"\bp\s*&\s*l\b|\bplan\b|\bbudget\b",
    re.I,
)

# Files we deliberately skip even when they're in the right folder. Cap tables,
# 409As, comps, and waterfalls are spreadsheets but they're not the multi-year
# operating model we want to extract from.
NEGATIVE_NAME_PATTERNS = re.compile(
    r"cap.?table|waterfall|409a|valuation.?comp|"
    r"deal.?summary|term.?sheet|wire|kyc",
    re.I,
)

XLSX_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}
GSHEET_MIME = "application/vnd.google-apps.spreadsheet"


def _is_financial_model(file_meta: dict) -> bool:
    """Decide if a Drive file looks like a financial model worth extracting."""
    name = file_meta.get("name", "")
    mime = file_meta.get("mimeType", "")
    if NEGATIVE_NAME_PATTERNS.search(name):
        return False
    if MODEL_NAME_PATTERNS.search(name):
        return True
    # No name match → only accept if it's a spreadsheet AND lives in a folder
    # we trust (handled by the caller — we only walk model-bearing folders).
    return mime in XLSX_MIMES or mime == GSHEET_MIME


# ── Year detection ───────────────────────────────────────────────────────────
# Try filename first ("2024 Operating Model.xlsx"), fall back to modifiedTime.
YEAR_IN_NAME = re.compile(r"\b(20\d{2})\b")
FY_IN_NAME   = re.compile(r"\bFY\s*(\d{2,4})\b", re.I)


def _classify_snapshot_year(file_meta: dict) -> int:
    """Heuristic year this model represents.

    Filename year wins ('2024 Forecast.xlsx' → 2024). FY shorthand is
    expanded ('FY24' → 2024). Falls back to the year in modifiedTime
    when neither pattern hits.
    """
    name = file_meta.get("name", "")
    m = YEAR_IN_NAME.search(name)
    if m:
        y = int(m.group(1))
        if 2000 <= y <= 2100:
            return y
    m = FY_IN_NAME.search(name)
    if m:
        raw = m.group(1)
        y = int(raw) if len(raw) == 4 else 2000 + int(raw)
        if 2000 <= y <= 2100:
            return y
    mod = file_meta.get("modifiedTime") or file_meta.get("modified") or ""
    try:
        return datetime.fromisoformat(mod.replace("Z", "+00:00")).year
    except Exception:
        return datetime.now().year


# ── Listing ──────────────────────────────────────────────────────────────────
def _list_models_in_drive(service, conn, company_id: int) -> list[dict]:
    """Walk every linked Drive folder for the company and return a deduped
    list of files that look like financial models.

    Dedupe key is Drive fileId (one Drive file = one snapshot, even if it's
    referenced from multiple linked folders). Each item carries enough
    metadata to download and persist it later.
    """
    from .drive_scan import _list_files_recursive

    folders = conn.execute(
        "SELECT * FROM pr_company_folders WHERE company_id=?", (company_id,)
    ).fetchall()
    if not folders:
        return []

    seen: dict[str, dict] = {}  # fileId → metadata
    for folder in folders:
        try:
            files = _list_files_recursive(service, folder["drive_folder_id"])
        except Exception as e:
            logger.warning(
                f"Drive list failed for folder {folder['drive_folder_name']}: {e}"
            )
            continue
        for f in files:
            if not _is_financial_model(f):
                continue
            fid = f.get("id")
            if not fid or fid in seen:
                continue
            seen[fid] = {
                "drive_file_id": fid,
                "name": f.get("name") or "(unnamed)",
                "mime_type": f.get("mimeType") or "",
                "modified": f.get("modifiedTime"),
                "size": f.get("size"),
                "url": f.get("webViewLink") or f"https://drive.google.com/file/d/{fid}/view",
                "folder_type": folder["folder_type"],
                "folder_name": folder["drive_folder_name"],
                "snapshot_year": _classify_snapshot_year(f),
            }
    # Newest snapshot year first — operator usually wants the latest at top
    return sorted(seen.values(), key=lambda m: (m["snapshot_year"], m.get("modified") or ""), reverse=True)


# ── Download ─────────────────────────────────────────────────────────────────
def _download_xlsx_to_tempfile(service, file_meta: dict) -> str:
    """Stream a Drive file to a tempfile and return its path. Caller is
    responsible for unlinking the temp.

    Google Sheets get exported to xlsx; native xlsx is downloaded directly.
    """
    from googleapiclient.http import MediaIoBaseDownload

    mime = file_meta.get("mime_type") or ""
    if mime == GSHEET_MIME:
        request = service.files().export_media(
            fileId=file_meta["drive_file_id"],
            mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        request = service.files().get_media(
            fileId=file_meta["drive_file_id"], supportsAllDrives=True
        )
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    # Write to a tempfile with .xlsx suffix so run_pipeline's filetype check passes
    tmp = tempfile.NamedTemporaryFile(prefix="pr_finmodel_", suffix=".xlsx", delete=False)
    tmp.write(buf.getvalue())
    tmp.flush()
    tmp.close()
    return tmp.name


# ── Persistence ──────────────────────────────────────────────────────────────
def _persist_snapshot(
    conn, *, company_id: int, file_meta: dict, result: Optional[dict],
    status: str, error: str = "",
) -> None:
    """UPSERT one snapshot. `result` is the full run_pipeline output (or None
    when extraction failed)."""
    financials = (result or {}).get("financials") or {}
    units      = (result or {}).get("units") or {}
    years      = (result or {}).get("fiscal_years") or []
    summary    = (result or {}).get("model_summary") or {}
    fy_end_m   = int((summary.get("fy_end_month") if isinstance(summary, dict) else None) or 12)

    conn.execute(
        """INSERT INTO pr_financial_snapshots
           (company_id, snapshot_year, source_file_id, source_file_name,
            source_modified, source_url, fy_end_month,
            financials_json, units_json, fiscal_years_json, model_summary,
            status, error_summary, extracted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(company_id, source_file_id) DO UPDATE SET
          snapshot_year     = excluded.snapshot_year,
          source_file_name  = excluded.source_file_name,
          source_modified   = excluded.source_modified,
          source_url        = excluded.source_url,
          fy_end_month      = excluded.fy_end_month,
          financials_json   = excluded.financials_json,
          units_json        = excluded.units_json,
          fiscal_years_json = excluded.fiscal_years_json,
          model_summary     = excluded.model_summary,
          status            = excluded.status,
          error_summary     = excluded.error_summary,
          extracted_at      = datetime('now')""",
        (
            company_id,
            int(file_meta["snapshot_year"]),
            file_meta["drive_file_id"],
            file_meta["name"],
            file_meta.get("modified"),
            file_meta.get("url") or "",
            fy_end_m,
            json.dumps(financials),
            json.dumps(units),
            json.dumps(years),
            json.dumps(summary if isinstance(summary, dict) else {}),
            status,
            (error or "")[:1000],
        ),
    )


# ── Public entry point ───────────────────────────────────────────────────────
def extract_company_history(conn, *, company_id: int, user_id: int,
                            fy_end_month: int = 12) -> dict:
    """End-to-end: list models in Drive → run extractor on each → persist.

    Returns a status dict suitable for the API:
      {
        company_id, company_name,
        models_found, models_extracted, models_failed,
        snapshots: [ {snapshot_year, file_name, status, error?} ... ],
      }
    """
    from ..routes.drive import _get_drive_service
    from ..engine.financial_pipeline import run_pipeline

    company = conn.execute(
        "SELECT id, name FROM pr_companies WHERE id=?", (company_id,)
    ).fetchone()
    if not company:
        raise ValueError(f"Company {company_id} not found")
    company_name = company["name"]

    try:
        service = _get_drive_service(user_id)
    except Exception as e:
        raise ValueError(
            f"Drive auth failed for {company_name}: {e}. "
            f"Connect Google Drive in the underwriting app first."
        )

    models = _list_models_in_drive(service, conn, company_id)
    if not models:
        return {
            "company_id": company_id,
            "company_name": company_name,
            "models_found": 0,
            "models_extracted": 0,
            "models_failed": 0,
            "snapshots": [],
            "message": "No financial models found in linked Drive folders. "
                       "Run folder discovery + check that the company has a "
                       "model file (e.g. 'Operating Model.xlsx') in its folder.",
        }

    snapshots: list[dict] = []
    n_ok = n_failed = 0
    for m in models:
        tmp_path: Optional[str] = None
        try:
            tmp_path = _download_xlsx_to_tempfile(service, m)
            result = run_pipeline(
                input_path=tmp_path,
                company_id=str(company_id),
                fy_end_month=fy_end_month,
                out_dir=tempfile.gettempdir(),
            )
            status = (result.get("status") or "success").lower()
            if status not in {"success", "partial"}:
                status = "partial"
            _persist_snapshot(conn, company_id=company_id, file_meta=m,
                              result=result, status=status)
            n_ok += 1
            snapshots.append({
                "snapshot_year": m["snapshot_year"],
                "file_name": m["name"],
                "status": status,
                "fiscal_years": result.get("fiscal_years") or [],
            })
        except Exception as e:
            logger.exception(f"Extraction failed for {m['name']}")
            _persist_snapshot(conn, company_id=company_id, file_meta=m,
                              result=None, status="failed", error=str(e))
            n_failed += 1
            snapshots.append({
                "snapshot_year": m["snapshot_year"],
                "file_name": m["name"],
                "status": "failed",
                "error": str(e)[:300],
            })
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    conn.commit()
    return {
        "company_id": company_id,
        "company_name": company_name,
        "models_found": len(models),
        "models_extracted": n_ok,
        "models_failed": n_failed,
        "snapshots": snapshots,
    }


def list_company_history(conn, company_id: int) -> dict:
    """Return all stored snapshots for a company plus a pre-pivoted
    plan-vs-actuals matrix the UI can render directly without re-shaping."""
    rows = conn.execute(
        """SELECT * FROM pr_financial_snapshots
            WHERE company_id=? ORDER BY snapshot_year DESC, extracted_at DESC""",
        (company_id,),
    ).fetchall()
    snapshots: list[dict] = []
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else {}
        try:
            d["financials"] = json.loads(d.get("financials_json") or "{}")
        except Exception:
            d["financials"] = {}
        try:
            d["units"] = json.loads(d.get("units_json") or "{}")
        except Exception:
            d["units"] = {}
        try:
            d["fiscal_years"] = json.loads(d.get("fiscal_years_json") or "[]")
        except Exception:
            d["fiscal_years"] = []
        try:
            d["model_summary"] = json.loads(d.get("model_summary") or "{}")
        except Exception:
            d["model_summary"] = {}
        # Strip raw JSON fields from the response — UI uses the parsed shapes
        for k in ("financials_json", "units_json", "fiscal_years_json"):
            d.pop(k, None)
        snapshots.append(d)

    # Plan-vs-actuals matrix — collect every (metric, fy) pair seen across
    # snapshots, then for each cell record what each snapshot said. The UI
    # renders metrics as rows, fiscal years as columns, and shows the
    # distribution of forecasts (with the "actual" — the most recent
    # snapshot's value for that fy when fy <= snapshot_year — bolded).
    metrics_seen: set[str] = set()
    years_seen: set[int] = set()
    for s in snapshots:
        for metric, series in (s.get("financials") or {}).items():
            metrics_seen.add(metric)
            for fy in (series or {}).keys():
                try:
                    years_seen.add(int(fy))
                except (TypeError, ValueError):
                    continue

    return {
        "company_id": company_id,
        "snapshots": snapshots,
        "metrics": sorted(metrics_seen),
        "fiscal_years": sorted(years_seen),
    }
