"""
Phase 2 (copy + verify) and Phase 3 (reorganize the copy).

Safety model (plan, Section 2b / 4 / 5):
  * the ORIGINAL drive is never touched -- we only ever read it;
  * we make a full copy and verify it is faithful (hash for hash) before moving
    anything;
  * we then reorganize *inside the copy*, moving (never deleting) every file into
    the standard 16-folder structure and logging each move, so the whole thing is
    reversible and reconcilable.

Phase 3 works in place on the copy: it builds COMPANIES/<Company>/<16 folders>/
and moves each catalogued file to exactly one destination
(PLACED->its bucket, SUPERSEDED/DUPLICATE->14_Historical, PENDING->15).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

from . import config as C
from .models import FileRecord

COMPANIES_DIR = "COMPANIES"
UNRESOLVED = "_Unresolved_Company"

# Documents that legitimately belong in two folders (plan, Risks). The file lives
# in its primary bucket and a lightweight pointer is left in the secondary one.
CROSS_REF_RULES = {
    "06": ["04"],   # a board deck is also financial reporting
}


def assert_safe_output(source_root: Path, out_root: Path) -> None:
    """Guarantee we never write into the live Drive (or into the source tree).

    The plan's #1 rule is that the original is never touched. Output going into a
    synced cloud folder would sync back to Drive, so we refuse it outright.
    """
    s = Path(source_root).resolve()
    o = Path(out_root).resolve()
    op = str(o)
    BANNED = ("/CloudStorage/", "GoogleDrive-", "/My Drive", "Dropbox", "OneDrive")
    for b in BANNED:
        if b in op:
            raise ValueError(
                f"Refusing to write output inside a synced cloud folder ({b}). "
                f"Choose a local --out path outside Drive. Got: {o}")
    if o == s or s in o.parents or o in s.parents:
        raise ValueError(f"Output {o} must be a separate folder, outside the source {s}.")


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_and_verify(source_root: Path, work_root: Path) -> dict:
    """Phase 2: replicate source -> work and confirm every file copied faithfully.

    Resilient to the live Drive: a file that cannot be read (e.g. an online-only
    placeholder that fails to materialize) is logged as a copy error rather than
    aborting the whole copy. Verification re-hashes every copied file.
    """
    source_root = source_root.resolve()
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True)

    src_hashes: dict[str, str] = {}
    copy_errors: list[str] = []
    for dirpath, dirnames, filenames in os.walk(source_root, onerror=lambda e: None):
        dirnames.sort()
        for fn in sorted(filenames):
            sp = Path(dirpath) / fn
            if fn.lower() in C.IGNORE_FILENAMES or fn.startswith(C.IGNORE_PREFIXES):
                continue
            rel = str(sp.relative_to(source_root))
            dp = work_root / rel
            try:
                dp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sp, dp)
                src_hashes[rel] = _hash(sp)
            except Exception as e:
                copy_errors.append(f"{rel} :: {type(e).__name__}")

    mismatches, missing = [], []
    for rel, h in src_hashes.items():
        cp = work_root / rel
        if not cp.exists():
            missing.append(rel)
        elif _hash(cp) != h:
            mismatches.append(rel)

    return {
        "n_source_files": len(src_hashes),
        "n_copy_errors": len(copy_errors),
        "copy_errors": copy_errors[:50],
        "n_missing_in_copy": len(missing),
        "n_hash_mismatch": len(mismatches),
        "missing": missing[:50],
        "mismatches": mismatches[:50],
        "faithful": not missing and not mismatches and not copy_errors,
    }


_ILLEGAL = re.compile(r'[:#?*"<>|\x00-\x1f]')


def sanitize_name(name: str) -> str:
    """Strip characters that trip up automated tools (plan, Section 1: naming)."""
    stem, dot, ext = name.rpartition(".")
    base = stem if dot else name
    base = _ILLEGAL.sub("_", base).strip().strip(".")
    base = re.sub(r"\s+", " ", base)
    out = f"{base}.{ext}" if dot else base
    return out or "untitled"


def _company_dirname(company: str) -> str:
    if company in ("", "UNKNOWN"):
        return UNRESOLVED
    return _ILLEGAL.sub("_", company).strip()


def _dest_bucket(rec: FileRecord) -> str:
    if rec.disposition in ("SUPERSEDED", "DUPLICATE"):
        return C.HISTORICAL_CODE
    if rec.disposition == "PENDING":
        return C.PENDING_CODE
    return rec.bucket_code


def _unique(path: Path, file_id: str) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    return path.with_name(f"{stem}__{file_id}{path.suffix}")


def build_empty_structure(work_root: Path, companies: list[str]) -> None:
    """Give every company the same 16 folders, even when some are empty."""
    base = work_root / COMPANIES_DIR
    for company in companies:
        cdir = base / _company_dirname(company)
        for b in C.BUCKETS:
            (cdir / b.slug).mkdir(parents=True, exist_ok=True)


def reorganize(records: list[FileRecord], work_root: Path, *, link: bool = False) -> dict:
    """Phase 3: place every catalogued file into the standard structure in `work_root`.

    link=False  -> MOVE the file from the verified copy at work_root/<source_rel>
                   (the real reorg; requires a prior copy_and_verify).
    link=True   -> create a SYMLINK at the destination pointing to the original file
                   (a plan/preview: shows the exact end-state tree and reconciles by
                   path, without copying bytes or downloading online-only Drive files).
    """
    companies = sorted({r.company if r.company not in ("", "UNKNOWN") else "UNKNOWN"
                        for r in records})
    build_empty_structure(work_root, companies)
    base = work_root / COMPANIES_DIR

    move_log: list[dict] = []
    for rec in records:
        src = Path(rec.source_path)
        copy_path = src if link else (work_root / rec.source_rel)
        if not link and not copy_path.exists():
            move_log.append({"file_id": rec.file_id, "from_rel": rec.source_rel,
                             "to_rel": "", "disposition": rec.disposition,
                             "reason": "MISSING-IN-COPY", "ok": False})
            continue

        code = _dest_bucket(rec)
        bucket = C.BUCKET_BY_CODE[code]
        cdir = base / _company_dirname(rec.company) / bucket.slug
        cdir.mkdir(parents=True, exist_ok=True)

        safe = sanitize_name(rec.filename)
        dest = _unique(cdir / safe, rec.file_id)
        if link:
            try:
                dest.symlink_to(src)
            except Exception as e:
                move_log.append({"file_id": rec.file_id, "from_rel": rec.source_rel,
                                 "to_rel": "", "disposition": rec.disposition,
                                 "reason": f"SYMLINK-FAILED:{type(e).__name__}", "ok": False})
                continue
        else:
            shutil.move(str(copy_path), str(dest))
        rec.dest_rel = str(dest.relative_to(work_root))

        # cross-references: a doc that legitimately belongs in two folders lives in
        # its primary bucket and leaves a lightweight pointer in the secondary one
        # (plan, Risks: a board deck is also financial reporting). Only the current
        # copy is cross-referenced; superseded/duplicate copies are not.
        also = CROSS_REF_RULES.get(code, []) if rec.disposition == "PLACED" else []
        rec.also_relevant_to = list(also)
        for sec in also:
            sec_dir = base / _company_dirname(rec.company) / C.BUCKET_BY_CODE[sec].slug
            sec_dir.mkdir(parents=True, exist_ok=True)
            ptr = sec_dir / f"{Path(safe).stem}.cross-ref.txt"
            ptr.write_text(
                f"This document is filed under {bucket.code} {bucket.name}.\n"
                f"Primary location: {rec.dest_rel}\n"
                f"Cross-referenced here because it is also relevant to "
                f"{C.BUCKET_BY_CODE[sec].name}.\n",
                encoding="utf-8")

        move_log.append({
            "file_id": rec.file_id, "company": rec.company,
            "from_rel": rec.source_rel, "to_rel": rec.dest_rel,
            "disposition": rec.disposition, "bucket": f"{code} {bucket.name}",
            "renamed": (safe != rec.filename), "reason": "moved", "ok": True,
        })

    removed_dirs = _prune_empty_legacy(work_root)
    return {
        "n_moves": sum(1 for m in move_log if m["ok"]),
        "n_failed": sum(1 for m in move_log if not m["ok"]),
        "removed_empty_dirs": removed_dirs,
        "move_log": move_log,
        "companies": companies,
    }


def _prune_empty_legacy(work_root: Path) -> list[str]:
    """Remove legacy folders left empty after moves (never the new structure)."""
    removed: list[str] = []
    protected = {COMPANIES_DIR, "_CATALOG", "_REPORTS", "_LOGS"}
    # deepest-first so nested empties collapse cleanly
    for d in sorted((p for p in work_root.rglob("*") if p.is_dir()),
                    key=lambda p: len(p.parts), reverse=True):
        if COMPANIES_DIR in d.relative_to(work_root).parts:
            continue
        if d.relative_to(work_root).parts[0] in protected:
            continue
        try:
            if not any(d.iterdir()):
                rel = str(d.relative_to(work_root))
                d.rmdir()
                removed.append(rel)
        except Exception:
            pass
    return removed
