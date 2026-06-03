"""
Google Drive integration for portfolio_review.

Reuses the existing per-user OAuth flow from app.routes.drive — we don't
re-implement consent, refresh tokens, or credential storage. We just
borrow the authenticated `_get_drive_service(user_id)` helper and
download workbooks the user already has access to in their own Drive.

Two modes are supported on the source file:
    - Native .xlsx in Drive  → downloaded as-is via files.get_media
    - Google Sheets in Drive → exported as .xlsx via files.export

Once the file is on disk, the existing portfolio_review.loader.run_import
takes over. End-to-end: pick file → download → import → return counts.
"""

from __future__ import annotations

import io
import logging
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# MIME types we care about
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_XLS = "application/vnd.ms-excel"
MIME_GSHEET = "application/vnd.google-apps.spreadsheet"

# Drive search query: spreadsheets the user can read, ordered by recency.
# Covers native xlsx, legacy xls, and Google Sheets.
_LIST_QUERY = (
    f"(mimeType='{MIME_XLSX}' or mimeType='{MIME_XLS}' or mimeType='{MIME_GSHEET}') "
    "and trashed=false"
)


def list_spreadsheets(service, name_contains: Optional[str] = None,
                      page_size: int = 25) -> list[dict]:
    """List the user's spreadsheets ordered by last-modified desc.

    Filters by partial name match if `name_contains` is given.
    Returns a list of {id, name, mimeType, modifiedTime, owners, webViewLink}.
    """
    q = _LIST_QUERY
    if name_contains:
        # Drive search is full-text on name; quote-escape any embedded apostrophes.
        safe = name_contains.replace("'", "\\'")
        q += f" and name contains '{safe}'"
    resp = service.files().list(
        q=q,
        pageSize=page_size,
        orderBy="modifiedTime desc",
        fields="files(id, name, mimeType, modifiedTime, owners(displayName,emailAddress), webViewLink)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    return resp.get("files", [])


def download_spreadsheet(service, file_id: str) -> tuple[Path, dict]:
    """Download a spreadsheet from Drive to a temp .xlsx file.

    Handles both native xlsx (binary download) and Google Sheets (xlsx export).
    Returns (local_path, file_metadata).
    """
    from googleapiclient.http import MediaIoBaseDownload

    meta = service.files().get(
        fileId=file_id,
        fields="id, name, mimeType, modifiedTime, owners(displayName, emailAddress)",
        supportsAllDrives=True,
    ).execute()

    if meta.get("mimeType") == MIME_GSHEET:
        request = service.files().export_media(
            fileId=file_id,
            mimeType=MIME_XLSX,
        )
    else:
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)

    # Stream into a temp file
    suffix = ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="pr_drive_")
    try:
        with open(fd, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    return Path(tmp_path), meta


def sync_from_drive(file_id: str, conn: sqlite3.Connection,
                    user_id: int, as_of_date: Optional[str] = None) -> dict:
    """Full pipeline: download workbook from Drive, run portfolio_review loader.

    Reuses the import_drive_service helper from app.routes.drive — that
    handles refresh, revocation, and 401 mapping.
    """
    from ..routes.drive import _get_drive_service
    from .loader import run_import

    service = _get_drive_service(user_id)
    local_path, meta = download_spreadsheet(service, file_id)
    try:
        result = run_import(local_path, conn, user_id=user_id, as_of_date=as_of_date)
    finally:
        # Always clean up the temp file (whether import succeeded or not)
        local_path.unlink(missing_ok=True)

    # Annotate the result with what we synced
    result["source"] = {
        "drive_file_id": meta.get("id"),
        "drive_file_name": meta.get("name"),
        "drive_modified": meta.get("modifiedTime"),
    }
    return result
