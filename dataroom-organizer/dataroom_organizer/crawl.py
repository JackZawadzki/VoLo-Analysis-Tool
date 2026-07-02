"""
Phase 1 -- inventory + classify, strictly read-only.

Walks the original drive, and for every file builds one catalog row: identity and
provenance, the inferred company (even when the file is stranded in a cross-cutting
theme folder), a code-read of any numbers, a deterministic classification with
evidence and confidence, and the date / confidentiality / version signals later
phases need. Nothing is moved or modified here.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Optional

from . import config as C
from . import classify as CL
from .extract import extract
from .models import FileRecord


def _sha256(path: Path, full: bool = True) -> str:
    """Full content hash for exact-duplicate detection and reconciliation.

    full=False (light/preview mode) returns "" WITHOUT reading the file: on a
    Google Drive mount, reading any part of an online-only file forces a full
    download, so a fast preview must stay metadata-only. With an empty hash,
    exact-duplicate detection is simply skipped for that run."""
    if not full:
        return ""
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except Exception:
        return ""
    return h.hexdigest()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def discover_companies(source_root: Path, extra: Optional[list[str]] = None) -> dict[str, str]:
    """Build {normalized_alias: CanonicalName} from non-theme top-level folders.

    Aliases include the spaced, concatenated and de-punctuated forms so a file
    named 'NthCycle_captable.xlsx' still resolves to company 'Nth Cycle'.
    """
    canon: dict[str, str] = {}
    names: list[str] = []
    for child in sorted(source_root.iterdir()):
        if child.is_dir() and not C.is_theme_folder(child.name) and not child.name.startswith("_"):
            names.append(child.name)
    for n in (extra or []):
        if n not in names:
            names.append(n)

    for name in names:
        norm = _norm(name)
        if not norm:
            continue
        canon[norm] = name
        canon[norm.replace(" ", "")] = name
    return canon


def infer_company(blob: str, canon: dict[str, str]) -> tuple[str, str]:
    """Resolve a company from a text blob (filename + path). Returns (name, evidence)."""
    low = _norm(blob)
    flat = low.replace(" ", "")
    # Prefer the longest matching alias to avoid 'nth' matching before 'nth cycle'.
    for alias in sorted(canon, key=len, reverse=True):
        if not alias:
            continue
        if " " in alias:
            if re.search(rf"\b{re.escape(alias)}\b", low):
                return canon[alias], f"name-match:'{alias}'"
        else:
            if alias in flat:
                return canon[alias], f"token-match:'{alias}'"
    return "UNKNOWN", "no-company-match"


def _summary(bucket_name: str, text: str, filename: str) -> str:
    snippet = ""
    for line in (text or "").splitlines():
        s = line.strip()
        if len(s) >= 12 and not s.startswith("[sheet:"):
            snippet = re.sub(r"\s+", " ", s)[:90]
            break
    if snippet:
        return f"{bucket_name} — {snippet}"
    return f"{bucket_name} — {Path(filename).stem}"


def _should_ignore(path: Path) -> bool:
    n = path.name.lower()
    return n in C.IGNORE_FILENAMES or path.name.startswith(C.IGNORE_PREFIXES)


def crawl(source_root: Path, *, companies: Optional[list[str]] = None,
          llm_hook: Optional[CL.LLMHook] = None, single_company: Optional[str] = None,
          content_mode: str = "full", full_hash: bool = True) -> list[FileRecord]:
    """Phase 1 crawl.

    single_company: when set, the source root *is* one company's folder; every file
      belongs to that company and the top-level entries are intra-company sub-folders
      (used as path signals), not other companies or theme folders.
    content_mode ("full"/"fast"/"none") and full_hash: turn down for a fast,
      read-only preview of a large tree (see extract()).
    """
    source_root = source_root.resolve()
    canon = {} if single_company else discover_companies(source_root, extra=companies)
    records: list[FileRecord] = []
    counter = 0
    n_errors = 0

    # os.walk (not rglob) so one unreadable directory/file on the live Drive can't
    # abort the whole crawl; online-only placeholders are recorded, not fatal.
    for dirpath, dirnames, filenames in os.walk(source_root, onerror=lambda e: None):
        dirnames.sort()
        for fn in sorted(filenames):
            path = Path(dirpath) / fn
            if _should_ignore(path):
                continue
            rel = path.relative_to(source_root)
            try:
                rec = _build_record(path, rel, counter + 1, canon, single_company,
                                    content_mode, full_hash, llm_hook)
                counter += 1
                records.append(rec)
            except Exception as e:
                counter += 1
                n_errors += 1
                records.append(FileRecord(
                    file_id=f"F{counter:05d}", source_path=str(path), source_rel=str(rel),
                    filename=fn, ext=C.clean_ext(fn),
                    company=single_company or "UNKNOWN", company_evidence="error",
                    bucket_code=C.PENDING_CODE, bucket_name=C.BUCKET_BY_CODE[C.PENDING_CODE].name,
                    disposition="", needs_review=True, current=True,
                    summary=f"Unreadable / online-only: {fn}",
                    flags=["unreadable-or-online-only", f"error:{type(e).__name__}"]))
    if n_errors:
        print(f"  note: {n_errors} file(s) were unreadable/online-only and flagged for review")
    return records


def _build_record(path: Path, rel: Path, idx: int, canon: dict,
                  single_company: Optional[str], content_mode: str, full_hash: bool,
                  llm_hook) -> FileRecord:
    parts = rel.parts
    origin_folder = parts[0] if len(parts) > 1 else ""

    if single_company:
        cname, cevidence, origin_kind, theme_code = single_company, "source-root", "subfolder", None
    else:
        if origin_folder and C.is_theme_folder(origin_folder):
            origin_kind = "theme"
        elif origin_folder:
            origin_kind = "company"
        else:
            origin_kind = "root"
        if origin_kind == "company":
            cname = canon.get(_norm(origin_folder)) or origin_folder
            cevidence = "origin-folder"
        else:
            cname, cevidence = infer_company(str(rel), canon)
        theme_code = C.theme_bucket(origin_folder) if origin_kind == "theme" else None

    info = extract(path, content_mode=content_mode)
    text = info["text"]
    ext = C.clean_ext(path.name) or path.suffix.lower()

    cls = CL.classify(filename=path.name, source_rel=str(rel), text=text,
                      ext=ext, theme_code=theme_code, llm_hook=llm_hook)

    confidentiality = CL.parse_confidentiality(f"{path.name} {text[:500]}")
    stage = CL.parse_stage(path.name)
    fdate = CL.parse_date_from_name(path.name)
    mtime = CL.mtime_iso(path)
    doc_date = fdate or mtime
    doc_date_source = "filename" if fdate else "mtime"

    try:
        size = path.stat().st_size
    except Exception:
        size = 0

    rec = FileRecord(
        file_id=f"F{idx:05d}",
        source_path=str(path),
        source_rel=str(rel),
        filename=path.name,
        ext=ext,
        size_bytes=size,
        mtime_iso=mtime,
        sha256=_sha256(path, full=full_hash),
        company=cname,
        company_evidence=cevidence,
        origin_folder=origin_folder,
        origin_kind=origin_kind,
        bucket_code=cls["code"],
        bucket_name=cls["bucket_name"],
        confidence=cls["confidence"],
        needs_review=cls["needs_review"],
        evidence=cls["evidence"] + [f"method:{cls['method']}"],
        doc_date=doc_date,
        doc_date_source=doc_date_source,
        confidentiality=confidentiality,
        stage=stage,
        text_chars=len(text or ""),
        gnative_url=info.get("gnative", {}).get("url", ""),
        extracted_numbers=info["numbers"],
        summary=_summary(cls["bucket_name"], text, path.name),
        flags=list(info["flags"]),
    )
    if cname == "UNKNOWN":
        rec.flags.append("company-unresolved")
        rec.needs_review = True
    # We could not read the contents (image-only / no text layer / opaque binary):
    # place by filename but flag for a human, since content evidence is missing.
    if any(f in rec.flags for f in ("image-only", "pdf-no-text-layer", "legacy-binary-format")):
        rec.needs_review = True
    return rec

    return records
