"""
Google Drive integration — sync deal data room folders into the document library.

POST   /api/drive/libraries              — create / link a deal library to a Drive folder
GET    /api/drive/libraries              — list all libraries
GET    /api/drive/libraries/{id}         — get library details + documents
DELETE /api/drive/libraries/{id}         — remove a library
POST   /api/drive/libraries/{id}/sync    — sync from Google Drive (pull new/changed files)
GET    /api/drive/libraries/{id}/documents — list documents in a library
PUT    /api/drive/documents/{id}/category — update document category
"""

import hashlib
import io
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..auth import CurrentUser, get_current_user
from ..database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/drive", tags=["drive"])

# ── Temp directory for downloaded files during extraction ─────────────────────
_DRIVE_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "drive_cache"
_DRIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Supported file types for text extraction
_EXTRACTABLE_MIMES = {
    'application/pdf': '.pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
    'text/plain': '.txt',
    'text/csv': '.csv',
    'text/markdown': '.md',
    'application/json': '.json',
    'text/html': '.html',
}

# Google Docs/Sheets/Slides need to be exported
_GOOGLE_EXPORT_MIMES = {
    'application/vnd.google-apps.document': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'),
    'application/vnd.google-apps.spreadsheet': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
    'application/vnd.google-apps.presentation': ('application/vnd.openxmlformats-officedocument.presentationml.presentation', '.pptx'),
}

# Category inference from filename / mime
_CATEGORY_PATTERNS = {
    'financial_model': ['financial', 'model', 'proforma', 'pro forma', 'forecast', 'projections', 'budget'],
    'pitch_deck': ['pitch', 'deck', 'presentation', 'investor update'],
    'term_sheet': ['term sheet', 'terms', 'loi', 'letter of intent'],
    'cap_table': ['cap table', 'captable', 'ownership'],
    'legal': ['legal', 'contract', 'agreement', 'nda', 'msa', 'incorporation', 'bylaws'],
    'ip_patent': ['patent', 'ip', 'intellectual property', 'trademark'],
    'customer_reference': ['customer', 'reference', 'testimonial', 'case study'],
    'market_research': ['market', 'research', 'analysis', 'tam', 'landscape', 'industry'],
    'technical_diligence': ['technical', 'diligence', 'architecture', 'engineering', 'tech'],
    'team_bios': ['team', 'bio', 'leadership', 'management', 'founders'],
    'board_materials': ['board', 'minutes', 'governance'],
}


def _infer_category(file_name: str) -> str:
    """Guess document category from filename."""
    name_lower = file_name.lower()
    for category, keywords in _CATEGORY_PATTERNS.items():
        if any(kw in name_lower for kw in keywords):
            return category
    return 'other'


def _parse_drive_folder_id(url_or_id: str) -> str:
    """Extract Google Drive folder ID from a URL or raw ID."""
    url_or_id = url_or_id.strip()
    # Direct ID (no slashes)
    if re.match(r'^[a-zA-Z0-9_-]{10,}$', url_or_id):
        return url_or_id
    # URL patterns
    m = re.search(r'/folders/([a-zA-Z0-9_-]+)', url_or_id)
    if m:
        return m.group(1)
    m = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url_or_id)
    if m:
        return m.group(1)
    raise ValueError(f"Could not parse Drive folder ID from: {url_or_id[:100]}")


def _get_drive_service():
    """Build a Google Drive API service using service account credentials."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="Google API libraries not installed. Run: pip install google-api-python-client google-auth"
        )

    creds_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not creds_path:
        # Also check for inline JSON in env
        creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_CREDS", "")
        if creds_json:
            import json as _json
            info = _json.loads(creds_json)
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="Google Drive credentials not configured. Set GOOGLE_SERVICE_ACCOUNT_JSON (path) or GOOGLE_SERVICE_ACCOUNT_CREDS (JSON string) in .env"
            )
    else:
        creds = service_account.Credentials.from_service_account_file(
            creds_path, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list_files_recursive(service, folder_id: str, path_prefix: str = "") -> list:
    """Recursively list all files in a Drive folder and subfolders."""
    all_files = []
    page_token = None

    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
            pageSize=200,
            pageToken=page_token,
        ).execute()

        for f in resp.get("files", []):
            if f["mimeType"] == "application/vnd.google-apps.folder":
                # Recurse into subfolder
                subfolder_path = f"{path_prefix}{f['name']}/"
                all_files.extend(_list_files_recursive(service, f["id"], subfolder_path))
            else:
                f["subfolder_path"] = path_prefix
                all_files.append(f)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return all_files


def _download_file(service, file_info: dict) -> bytes:
    """Download a file from Drive. Handles Google Docs export."""
    mime = file_info["mimeType"]
    file_id = file_info["id"]

    if mime in _GOOGLE_EXPORT_MIMES:
        export_mime, _ = _GOOGLE_EXPORT_MIMES[mime]
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
    else:
        request = service.files().get_media(fileId=file_id)

    from googleapiclient.http import MediaIoBaseDownload
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def _extract_text(file_bytes: bytes, file_name: str, mime_type: str) -> str:
    """Extract text from file bytes. Reuses the same logic as memo.py."""
    # Determine the effective extension
    ext = Path(file_name).suffix.lower()
    if mime_type in _GOOGLE_EXPORT_MIMES:
        _, ext = _GOOGLE_EXPORT_MIMES[mime_type]

    text = ""
    try:
        if ext in ('.txt', '.md', '.csv', '.json', '.html'):
            text = file_bytes.decode('utf-8', errors='replace')[:100_000]

        elif ext == '.pdf':
            try:
                import fitz
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                pages = [page.get_text() for page in doc]
                text = "\n\n".join(pages)[:200_000]
                doc.close()
            except ImportError:
                text = "[PDF text extraction requires PyMuPDF]"

        elif ext == '.docx':
            try:
                from docx import Document
                doc = Document(io.BytesIO(file_bytes))
                paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                text = "\n".join(paragraphs)[:200_000]
            except ImportError:
                text = "[DOCX text extraction requires python-docx]"

        elif ext in ('.xlsx', '.xls'):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
                parts = []
                for ws in wb.worksheets[:10]:
                    parts.append(f"=== Sheet: {ws.title} ===")
                    row_count = 0
                    for row in ws.iter_rows(values_only=True):
                        vals = [str(c) if c is not None else '' for c in row]
                        if any(v.strip() for v in vals):
                            parts.append('\t'.join(vals))
                            row_count += 1
                            if row_count > 300:
                                parts.append(f"... (truncated, {ws.max_row} total rows)")
                                break
                wb.close()
                text = "\n".join(parts)[:200_000]
            except ImportError:
                text = "[Excel text extraction requires openpyxl]"

        elif ext == '.pptx':
            try:
                from pptx import Presentation
                prs = Presentation(io.BytesIO(file_bytes))
                parts = []
                for i, slide in enumerate(prs.slides):
                    parts.append(f"=== Slide {i+1} ===")
                    for shape in slide.shapes:
                        if shape.has_text_frame:
                            for para in shape.text_frame.paragraphs:
                                if para.text.strip():
                                    parts.append(para.text.strip())
                text = "\n".join(parts)[:200_000]
            except ImportError:
                text = "[PPTX text extraction requires python-pptx]"

    except Exception as e:
        text = f"[Extraction error: {str(e)[:200]}]"

    return text


# ═══════════════════════════════════════════════════════════════════════════════
#  LIBRARY CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class LibraryCreate(BaseModel):
    company_name: str
    drive_folder_url: str


class LibraryUpdate(BaseModel):
    company_name: Optional[str] = None
    drive_folder_url: Optional[str] = None


@router.post("/libraries")
async def create_library(req: LibraryCreate, user: CurrentUser = Depends(get_current_user)):
    try:
        folder_id = _parse_drive_folder_id(req.drive_folder_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    conn = get_db()
    try:
        # Check if library already exists for this folder
        existing = conn.execute(
            "SELECT id FROM deal_document_libraries WHERE owner_id=? AND drive_folder_id=?",
            (user.id, folder_id),
        ).fetchone()
        if existing:
            return {"id": existing["id"], "already_exists": True}

        cur = conn.execute(
            """INSERT INTO deal_document_libraries (owner_id, company_name, drive_folder_id, drive_folder_url)
               VALUES (?, ?, ?, ?)""",
            (user.id, req.company_name, folder_id, req.drive_folder_url),
        )
        conn.commit()
        return {"id": cur.lastrowid, "folder_id": folder_id}
    finally:
        conn.close()


@router.get("/libraries")
async def list_libraries(user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, company_name, drive_folder_id, drive_folder_url, last_synced_at,
                      sync_status, doc_count, created_at
               FROM deal_document_libraries WHERE owner_id=? ORDER BY updated_at DESC""",
            (user.id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/libraries/{lib_id}")
async def get_library(lib_id: int, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM deal_document_libraries WHERE id=? AND owner_id=?",
            (lib_id, user.id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Library not found")
        lib = dict(row)

        # Include documents
        docs = conn.execute(
            """SELECT id, file_name, file_type, file_size, mime_type, subfolder_path,
                      doc_category, drive_modified, last_extracted,
                      LENGTH(extracted_text) as extracted_chars
               FROM deal_documents WHERE library_id=? AND owner_id=?
               ORDER BY subfolder_path, file_name""",
            (lib_id, user.id),
        ).fetchall()
        lib["documents"] = [dict(d) for d in docs]
        return lib
    finally:
        conn.close()


@router.delete("/libraries/{lib_id}")
async def delete_library(lib_id: int, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        conn.execute("DELETE FROM deal_documents WHERE library_id=? AND owner_id=?", (lib_id, user.id))
        conn.execute("DELETE FROM deal_document_libraries WHERE id=? AND owner_id=?", (lib_id, user.id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  SYNC — pull files from Google Drive
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/libraries/{lib_id}/sync")
async def sync_library(lib_id: int, user: CurrentUser = Depends(get_current_user)):
    """
    Sync a library from Google Drive:
    1. List all files recursively in the Drive folder
    2. For new/changed files: download, extract text, store in DB
    3. Remove DB records for files deleted from Drive
    """
    conn = get_db()
    try:
        lib = conn.execute(
            "SELECT * FROM deal_document_libraries WHERE id=? AND owner_id=?",
            (lib_id, user.id),
        ).fetchone()
        if not lib:
            raise HTTPException(status_code=404, detail="Library not found")
        folder_id = lib["drive_folder_id"]
    finally:
        conn.close()

    # Update status to syncing
    conn = get_db()
    try:
        conn.execute(
            "UPDATE deal_document_libraries SET sync_status='syncing', updated_at=datetime('now') WHERE id=?",
            (lib_id,),
        )
        conn.commit()
    finally:
        conn.close()

    start_time = time.time()
    stats = {"new": 0, "updated": 0, "unchanged": 0, "removed": 0, "errors": 0, "skipped": 0}

    try:
        service = _get_drive_service()

        # List all files in the folder recursively
        drive_files = _list_files_recursive(service, folder_id)
        drive_file_ids = set()

        for f in drive_files:
            drive_file_ids.add(f["id"])
            mime = f.get("mimeType", "")
            name = f.get("name", "unknown")
            modified = f.get("modifiedTime", "")
            size = int(f.get("size", 0)) if f.get("size") else 0
            subfolder = f.get("subfolder_path", "")

            # Check if this file type is extractable
            is_extractable = mime in _EXTRACTABLE_MIMES or mime in _GOOGLE_EXPORT_MIMES
            if not is_extractable:
                # Check by extension
                ext = Path(name).suffix.lower()
                if ext not in ('.pdf', '.docx', '.xlsx', '.pptx', '.txt', '.csv', '.md', '.json', '.html'):
                    stats["skipped"] += 1
                    continue

            # Check if we already have this file
            conn = get_db()
            try:
                existing = conn.execute(
                    "SELECT id, drive_modified, extraction_hash FROM deal_documents WHERE library_id=? AND drive_file_id=?",
                    (lib_id, f["id"]),
                ).fetchone()
            finally:
                conn.close()

            if existing and existing["drive_modified"] == modified:
                # File unchanged
                stats["unchanged"] += 1
                continue

            # Download and extract
            try:
                file_bytes = _download_file(service, f)
                text = _extract_text(file_bytes, name, mime)
                text_hash = hashlib.md5(text.encode()).hexdigest()

                ext = Path(name).suffix.lower()
                if mime in _GOOGLE_EXPORT_MIMES:
                    _, ext = _GOOGLE_EXPORT_MIMES[mime]

                category = _infer_category(name)

                conn = get_db()
                try:
                    if existing:
                        # Update existing record
                        conn.execute(
                            """UPDATE deal_documents SET
                                file_name=?, file_type=?, file_size=?, mime_type=?,
                                subfolder_path=?, doc_category=?, extracted_text=?,
                                extraction_hash=?, drive_modified=?,
                                last_extracted=datetime('now'), updated_at=datetime('now')
                               WHERE id=?""",
                            (name, ext, size, mime, subfolder, category, text,
                             text_hash, modified, existing["id"]),
                        )
                        stats["updated"] += 1
                    else:
                        # Insert new record
                        conn.execute(
                            """INSERT INTO deal_documents
                               (library_id, owner_id, drive_file_id, file_name, file_type,
                                file_size, mime_type, subfolder_path, doc_category,
                                extracted_text, extraction_hash, drive_modified, last_extracted)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                            (lib_id, user.id, f["id"], name, ext, size, mime,
                             subfolder, category, text, text_hash, modified),
                        )
                        stats["new"] += 1
                    conn.commit()
                finally:
                    conn.close()

            except Exception as e:
                logger.error(f"Drive sync: failed to process {name}: {e}")
                stats["errors"] += 1
                continue

        # Remove documents that are no longer in Drive
        conn = get_db()
        try:
            existing_docs = conn.execute(
                "SELECT id, drive_file_id FROM deal_documents WHERE library_id=?",
                (lib_id,),
            ).fetchall()
            for doc in existing_docs:
                if doc["drive_file_id"] not in drive_file_ids:
                    conn.execute("DELETE FROM deal_documents WHERE id=?", (doc["id"],))
                    stats["removed"] += 1
            conn.commit()
        finally:
            conn.close()

        # Update library metadata
        elapsed = round(time.time() - start_time, 2)
        conn = get_db()
        try:
            total_docs = conn.execute(
                "SELECT COUNT(*) as c FROM deal_documents WHERE library_id=?", (lib_id,),
            ).fetchone()["c"]
            conn.execute(
                """UPDATE deal_document_libraries SET
                    sync_status='synced', last_synced_at=datetime('now'),
                    doc_count=?, updated_at=datetime('now')
                   WHERE id=?""",
                (total_docs, lib_id),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "status": "synced",
            "elapsed_s": elapsed,
            "total_docs": total_docs,
            "stats": stats,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Drive sync failed for library {lib_id}: {e}")
        conn = get_db()
        try:
            conn.execute(
                "UPDATE deal_document_libraries SET sync_status='error', updated_at=datetime('now') WHERE id=?",
                (lib_id,),
            )
            conn.commit()
        finally:
            conn.close()
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)[:300]}")


# ═══════════════════════════════════════════════════════════════════════════════
#  DOCUMENT MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/libraries/{lib_id}/documents")
async def list_library_documents(lib_id: int, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        docs = conn.execute(
            """SELECT id, file_name, file_type, file_size, mime_type, subfolder_path,
                      doc_category, drive_modified, last_extracted,
                      LENGTH(extracted_text) as extracted_chars
               FROM deal_documents WHERE library_id=? AND owner_id=?
               ORDER BY subfolder_path, file_name""",
            (lib_id, user.id),
        ).fetchall()
        return [dict(d) for d in docs]
    finally:
        conn.close()


class CategoryUpdate(BaseModel):
    category: str


@router.put("/documents/{doc_id}/category")
async def update_document_category(doc_id: int, req: CategoryUpdate, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE deal_documents SET doc_category=?, updated_at=datetime('now') WHERE id=? AND owner_id=?",
            (req.category, doc_id, user.id),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPER — Load documents from library for memo generation
# ═══════════════════════════════════════════════════════════════════════════════

def load_library_documents(library_id: int, owner_id: int) -> list:
    """Load all extracted documents from a deal library for use in memo generation.
    Returns list of dicts compatible with the memo pipeline's expected format."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, file_name, doc_category, extracted_text, subfolder_path
               FROM deal_documents WHERE library_id=? AND owner_id=?
               ORDER BY subfolder_path, doc_category, file_name""",
            (library_id, owner_id),
        ).fetchall()
        return [dict(r) for r in rows if (r["extracted_text"] or "").strip()]
    finally:
        conn.close()
