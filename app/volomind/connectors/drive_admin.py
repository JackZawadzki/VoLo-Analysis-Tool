"""Google Drive admin connector — reuses the host app's per-user OAuth.

Strategy: an admin clicks "Sync Drive" in the VoLo Mind tab. We load THEIR
encrypted refresh token from the existing `user_drive_credentials` table
(populated by the IC memo Drive integration), rebuild a Credentials object,
and walk a configured Drive folder.

This means:
- No new OAuth app, client_id, or consent screen needed.
- The admin's existing IC-memo Drive connection IS their VoLo Mind admin auth.
- The data ends up in the SHARED volomind DB, accessible to all users.

Config (set when registering the source):
- root_folder_id: the Drive folder ID to walk (e.g. a Shared Drive's ID, or
  a specific folder within it). Required.
- co_type: 'portfolio' | 'potential' | None. Stamped onto every doc's
  source_metadata so the Tier 1 tagger can emit a co_type tag.
- max_file_bytes: per-file size cap (default 50MB). Files larger are skipped.
- admin_user_id: the user_id whose stored Drive creds we should use. Set by
  the route handler at sync time, not by the admin UI.

Native Google formats (Docs/Slides) are exported via files.export_media() to
docx/pptx so the extractors can read them. Sheets/Excel are skipped for v1.
"""

from __future__ import annotations

import io
import os
import threading
from datetime import datetime
from typing import Any, Iterable, Optional

from ..models import RawDocument
from .. import extractors
from .base import SourceConnector


_FOLDER_MIME = "application/vnd.google-apps.folder"

_GDOC_EXPORT: dict[str, str] = {
    "application/vnd.google-apps.document":
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.google-apps.presentation":
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_DRIVE_FILE_FIELDS = (
    "id,name,mimeType,parents,modifiedTime,createdTime,size,"
    "webViewLink,owners(emailAddress,displayName)"
)
_LIST_FIELDS = f"nextPageToken,files({_DRIVE_FILE_FIELDS})"

_DEFAULT_MAX_BYTES = 50 * 1024 * 1024


class DriveAdminConnector(SourceConnector):
    source_id = "gdrive_admin"
    # Marker read by the route handler. When True, the route uses
    # iter_file_metadata + process_file inside a ThreadPoolExecutor for
    # parallel ingestion. When False (or absent), the route falls back to
    # sequential list_documents().
    SUPPORTS_PARALLEL = True

    def __init__(self, *, config: dict[str, Any], cursor: Optional[str] = None):
        super().__init__(config=config, cursor=cursor)
        self._root_folder_id = self.config.get("root_folder_id")
        if not self._root_folder_id:
            raise ValueError("gdrive_admin connector requires config.root_folder_id")
        self._co_type = self.config.get("co_type")
        self._max_bytes = int(self.config.get("max_file_bytes", _DEFAULT_MAX_BYTES))
        self._admin_user_id = self.config.get("admin_user_id")
        if not self._admin_user_id:
            raise ValueError(
                "gdrive_admin connector requires config.admin_user_id "
                "(set by the route handler at sync time)"
            )
        self._latest_seen: Optional[str] = cursor
        # Thread-local Drive service. googleapiclient services are NOT thread
        # safe — each worker thread gets its own via _service(). The
        # underlying Credentials object IS thread-safe (auto-refresh handled
        # internally) and is shared.
        self._creds = None
        self._thread_local = threading.local()
        # Guards _latest_seen updates from concurrent process_file calls.
        self._latest_seen_lock = threading.Lock()

    # --- Auth via the host app's existing user_drive_credentials --------

    def _credentials(self):
        """Build / cache the OAuth Credentials object. Shared across threads
        (Credentials is documented thread-safe; auto-refresh internal lock)."""
        if self._creds is not None:
            return self._creds
        # Reuse the host app's OAuth credential loader. This pulls the admin's
        # encrypted refresh token from user_drive_credentials and rebuilds a
        # Credentials object using the same Fernet key as the IC memo flow.
        from ...routes.drive import _load_user_oauth_credentials, _touch_last_used

        creds = _load_user_oauth_credentials(self._admin_user_id)
        if creds is None:
            raise RuntimeError(
                f"Admin user {self._admin_user_id} has not connected Google Drive. "
                "Connect Drive via the IC Memo tab first, then retry the sync."
            )
        _touch_last_used(self._admin_user_id)
        self._creds = creds
        return creds

    def _build_service(self):
        """Return THIS thread's Drive service. Builds + caches lazily.

        googleapiclient service objects are NOT thread-safe per Google's
        docs ("An instance of a service object should not be used
        concurrently by multiple threads"). With parallel ingestion we
        need one per worker thread.
        """
        existing = getattr(self._thread_local, "service", None)
        if existing is not None:
            return existing
        try:
            from googleapiclient.discovery import build
        except ImportError as e:
            raise RuntimeError(
                "google-api-python-client not installed. "
                "pip install google-api-python-client google-auth"
            ) from e
        service = build("drive", "v3", credentials=self._credentials(), cache_discovery=False)
        self._thread_local.service = service
        return service

    # --- Listing --------------------------------------------------------

    def _list(self, q: str, *, order_by: Optional[str] = None) -> Iterable[dict]:
        service = self._build_service()
        page_token: Optional[str] = None
        while True:
            # corpora="allDrives" is REQUIRED to find Shared Drive content.
            # Default `corpora=user` only sees My Drive items even with
            # includeItemsFromAllDrives=True. Per Google Drive API docs:
            # to search across My Drive AND all shared drives the user can
            # access, both flags must be set together.
            req = service.files().list(
                corpora="allDrives",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                q=q,
                pageSize=1000,
                fields=_LIST_FIELDS,
                orderBy=order_by,
                pageToken=page_token,
            )
            page = req.execute()
            for f in page.get("files", []):
                yield f
            page_token = page.get("nextPageToken")
            if not page_token:
                break

    def _build_folder_map(self) -> dict[str, dict[str, Any]]:
        """id -> {name, parent_id} for every folder reachable from the root."""
        m: dict[str, dict[str, Any]] = {}
        # Walk descendants from root_folder_id by listing folders whose parent
        # is in our growing frontier set.
        frontier = {self._root_folder_id}
        seen = set()
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            q = f"mimeType = '{_FOLDER_MIME}' and trashed = false and '{current}' in parents"
            for f in self._list(q):
                m[f["id"]] = {
                    "name": f["name"],
                    "parent_id": current,
                }
                if f["id"] not in seen:
                    frontier.add(f["id"])
        return m

    def _resolve_path(
        self,
        file_parents: list[str],
        folder_map: dict[str, dict[str, Any]],
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Walk parent chain back to root_folder_id."""
        if not file_parents:
            return None, None, None
        chain: list[str] = []
        company: Optional[str] = None
        company_folder_id: Optional[str] = None
        seen: set[str] = set()
        cur: Optional[str] = file_parents[0]
        while cur and cur != self._root_folder_id and cur not in seen:
            seen.add(cur)
            node = folder_map.get(cur)
            if not node:
                break
            chain.append(node["name"])
            if node["parent_id"] == self._root_folder_id:
                company = node["name"]
                company_folder_id = cur
            cur = node["parent_id"]
        chain.reverse()
        return (
            "/".join(chain) if chain else None,
            company,
            company_folder_id,
        )

    # --- Body fetch -----------------------------------------------------

    def _download(self, file_id: str, mime: str) -> Optional[bytes]:
        from googleapiclient.http import MediaIoBaseDownload

        service = self._build_service()
        if mime in _GDOC_EXPORT:
            req = service.files().export_media(fileId=file_id, mimeType=_GDOC_EXPORT[mime])
        else:
            req = service.files().get_media(fileId=file_id, supportsAllDrives=True)

        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, req, chunksize=4 * 1024 * 1024)
        done = False
        while not done:
            try:
                _, done = downloader.next_chunk()
            except Exception:
                return None
            if buf.tell() > self._max_bytes:
                return None
        return buf.getvalue()

    # --- Public interface ----------------------------------------------

    def iter_file_metadata(self) -> Iterable[dict]:
        """Yield lightweight file-metadata payloads ordered by modifiedTime.

        Each payload is a dict with the file metadata + a reference to the
        shared folder_map. The folder_map is built once at the start (slow,
        sequential) and is read-only after that, so it's safe to share
        across worker threads. Pre-filters out unsupported MIME types and
        oversized files cheaply, before any download.

        Designed to feed a ThreadPoolExecutor running process_file().
        """
        folder_map = self._build_folder_map()
        descendant_ids = {self._root_folder_id, *folder_map.keys()}

        for batch in _chunks(list(descendant_ids), 50):
            parent_clauses = " or ".join(f"'{pid}' in parents" for pid in batch)
            q_parts = [
                f"mimeType != '{_FOLDER_MIME}'",
                "trashed = false",
                f"({parent_clauses})",
            ]
            if self.cursor:
                q_parts.append(f"modifiedTime > '{self.cursor}'")
            q = " and ".join(q_parts)

            for f in self._list(q, order_by="modifiedTime"):
                mime = f["mimeType"]
                export_mime = _GDOC_EXPORT.get(mime, mime)
                if not extractors.supported_mime(export_mime):
                    continue
                size_str = f.get("size")
                size = int(size_str) if size_str else 0
                if mime not in _GDOC_EXPORT and size > self._max_bytes:
                    continue
                yield {"file": f, "folder_map": folder_map}

    def process_file(self, payload: dict) -> Optional[RawDocument]:
        """Download + extract a single file. THREAD-SAFE. NEVER RAISES.

        Builds (or reuses) a thread-local Drive service, downloads bytes,
        runs the extractor, returns a RawDocument or None on
        skip/failure. Updates self._latest_seen under a lock so concurrent
        workers don't trample the cursor.

        Exceptions are caught and logged here rather than propagated so a
        single broken file doesn't crash ThreadPoolExecutor.map() iteration
        and stall the entire sync. Net effect: the broken file is silently
        skipped (returns None), counted in neither inserted nor errors.
        """
        try:
            return self._file_to_raw(payload["file"], payload["folder_map"])
        except Exception as e:
            file_id = (payload.get("file") or {}).get("id", "?")
            print(
                f"[drive_admin] process_file failed for {file_id}: {e}",
                flush=True,
            )
            return None

    def list_documents(self) -> Iterable[RawDocument]:
        """Sequential ingestion (backward-compat). The route handler uses
        iter_file_metadata + process_file with a ThreadPoolExecutor when
        SUPPORTS_PARALLEL is True; this method exists for the fallback
        path and for tests."""
        for payload in self.iter_file_metadata():
            doc = self.process_file(payload)
            if doc is not None:
                yield doc

    def _file_to_raw(self, f: dict, folder_map: dict[str, dict[str, Any]]) -> Optional[RawDocument]:
        mime: str = f["mimeType"]
        export_mime = _GDOC_EXPORT.get(mime, mime)
        if not extractors.supported_mime(export_mime):
            return None

        size_str = f.get("size")
        size = int(size_str) if size_str else 0
        if mime not in _GDOC_EXPORT and size > self._max_bytes:
            return None

        data = self._download(f["id"], mime)
        if data is None:
            return None
        text = extractors.extract(export_mime, data)
        if not text or not text.strip():
            return None

        folder_path, company, company_folder_id = self._resolve_path(
            f.get("parents") or [], folder_map,
        )
        modified_iso: Optional[str] = f.get("modifiedTime")
        modified_dt = _parse_iso(modified_iso)
        owners = f.get("owners") or []
        author = None
        if owners:
            author = owners[0].get("emailAddress") or owners[0].get("displayName")

        # Lock-protected check-and-set so concurrent workers don't race
        # and accidentally regress the cursor (each worker sees its own
        # value of _latest_seen if read unsynchronized).
        if modified_iso:
            with self._latest_seen_lock:
                if self._latest_seen is None or modified_iso > self._latest_seen:
                    self._latest_seen = modified_iso

        return RawDocument(
            source_doc_id=f["id"],
            title=f["name"],
            body_text=text,
            source_url=f.get("webViewLink"),
            occurred_at=modified_dt,
            folder_path=folder_path,
            author=author,
            source_metadata={
                "root_folder_id": self._root_folder_id,
                "mime_type": mime,
                "size_bytes": size,
                "company_name": company,
                "company_drive_folder_id": company_folder_id,
                "co_type": self._co_type,
            },
            source_updated_at=modified_dt,
        )

    def next_cursor(self) -> Optional[str]:
        return self._latest_seen


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]
