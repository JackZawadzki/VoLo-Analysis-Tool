"""
Validation + reconciliation reports -- the proof that nothing was lost.

Produces (plan, Section 3 "Validation reports"):
  * what was classified and placed, by company and bucket, with confidence;
  * duplicates and version conflicts found;
  * which folders are empty and which expected documents are missing per company;
  * a reconciliation proving every original file is accounted for, by count and by
    content hash.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter, defaultdict
from pathlib import Path

from . import config as C
from .organize import COMPANIES_DIR, _dest_bucket
from .models import FileRecord


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def reconcile(records: list[FileRecord], source_root: Path, work_root: Path,
              verify_hash: bool = True) -> dict:
    """Account for every original file. With verify_hash=True (a real byte copy) we
    confirm content hashes match; with verify_hash=False (a symlink plan/preview) we
    reconcile by count + existence only, so we never read (and download) file bytes."""
    source_hashes = Counter(r.sha256 for r in records if r.sha256)
    n_source = len(records)

    placed = [r for r in records if r.dest_rel]
    n_placed = len(placed)

    # every catalogued file should now exist at its dest. For a symlink plan we
    # check the symlink itself exists (lexists) -- not that its target is
    # materialized -- so online-only Drive files don't read as "missing".
    def _present(p: Path) -> bool:
        return p.exists() if verify_hash else os.path.lexists(p)
    missing_dest = [r.file_id for r in placed if not _present(work_root / r.dest_rel)]

    dest_hashes = Counter()
    bad_dest_hash = []
    if verify_hash:
        for r in placed:
            p = work_root / r.dest_rel
            if p.exists():
                h = _hash(p)
                dest_hashes[h] += 1
                if r.sha256 and h != r.sha256:
                    bad_dest_hash.append(r.file_id)

    hashes_preserved = (source_hashes == dest_hashes) if verify_hash else None
    unaccounted = [r.file_id for r in records if not r.dest_rel]

    ok = (len(unaccounted) == 0 and not missing_dest)
    if verify_hash:
        ok = ok and not bad_dest_hash and hashes_preserved

    return {
        "mode": "byte-copy + hash" if verify_hash else "symlink plan (path-only)",
        "n_source_files": n_source,
        "n_placed": n_placed,
        "n_unaccounted": len(unaccounted),
        "unaccounted": unaccounted[:50],
        "missing_at_dest": missing_dest[:50],
        "corrupted_at_dest": bad_dest_hash[:50],
        "distinct_source_hashes": len(source_hashes),
        "distinct_dest_hashes": len(dest_hashes),
        "content_hashes_preserved": hashes_preserved,
        "pass": ok,
    }


def summarize(records: list[FileRecord]) -> dict:
    by_company = defaultdict(list)
    for r in records:
        by_company[r.company if r.company not in ("", "UNKNOWN") else "UNKNOWN"].append(r)

    by_bucket = Counter(_dest_bucket(r) for r in records)
    dispositions = Counter(r.disposition for r in records)
    needs_review = [r for r in records if r.needs_review]
    conf_values = [r.confidence for r in records if r.disposition == "PLACED"]
    avg_conf = round(sum(conf_values) / len(conf_values), 3) if conf_values else 0.0

    duplicates = [r for r in records if r.disposition == "DUPLICATE"]
    superseded = [r for r in records if r.disposition == "SUPERSEDED"]
    conflicts = [r for r in records if "version-conflict" in r.flags]
    unresolved_co = [r for r in records if r.company in ("", "UNKNOWN")]

    # empty folders + missing expected, per company
    missing_expected = {}
    for company, recs in by_company.items():
        present = {_dest_bucket(r) for r in recs if r.current}
        miss = [f"{b.code} {b.name}" for b in C.BUCKETS
                if b.expected and b.code not in present]
        if miss:
            missing_expected[company] = miss

    return {
        "n_companies": len(by_company),
        "by_company": {k: len(v) for k, v in sorted(by_company.items())},
        "by_bucket": {f"{code} {C.BUCKET_BY_CODE[code].name}": by_bucket[code]
                      for code in sorted(by_bucket)},
        "dispositions": dict(dispositions),
        "avg_confidence_placed": avg_conf,
        "n_needs_review": len(needs_review),
        "needs_review": [{"file_id": r.file_id, "file": r.filename, "company": r.company,
                          "flags": r.flags, "confidence": r.confidence} for r in needs_review],
        "n_duplicates": len(duplicates),
        "n_superseded": len(superseded),
        "n_version_conflicts": len(conflicts),
        "n_company_unresolved": len(unresolved_co),
        "missing_expected": missing_expected,
    }


def write_reports(records: list[FileRecord], source_root: Path, work_root: Path,
                  reports_dir: Path, verify_hash: bool = True) -> dict:
    reports_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(records)
    recon = reconcile(records, source_root, work_root, verify_hash=verify_hash)

    md = ["# Validation & Reconciliation", "",
          "## Reconciliation — is every original file accounted for?", "",
          f"_Mode: {recon['mode']}_", ""]
    md += [
        f"- Original files catalogued: **{recon['n_source_files']}**",
        f"- Files placed in the reorganized output: **{recon['n_placed']}**",
        f"- Unaccounted-for files: **{recon['n_unaccounted']}**",
        f"- Missing at destination: **{len(recon['missing_at_dest'])}**",
    ]
    if recon["content_hashes_preserved"] is not None:
        md += [
            f"- Corrupted at destination (hash changed): **{len(recon['corrupted_at_dest'])}**",
            f"- Distinct content hashes — source {recon['distinct_source_hashes']} / "
            f"copy {recon['distinct_dest_hashes']}",
            f"- Content hashes preserved: **{recon['content_hashes_preserved']}**",
        ]
    md += [
        "",
        f"### Result: {'✅ PASS — nothing lost' if recon['pass'] else '❌ FAIL — investigate below'}",
        "",
    ]
    if not recon["pass"]:
        if recon["unaccounted"]:
            md += [f"- Unaccounted: {recon['unaccounted']}"]
        if recon["missing_at_dest"]:
            md += [f"- Missing at dest: {recon['missing_at_dest']}"]
        if recon["corrupted_at_dest"]:
            md += [f"- Corrupted: {recon['corrupted_at_dest']}"]

    md += ["", "## Classification & placement", "",
           f"- Companies: **{summary['n_companies']}**",
           f"- Average confidence (placed): **{summary['avg_confidence_placed']}**",
           f"- Dispositions: {summary['dispositions']}",
           "", "### Files per bucket", ""]
    for k, v in summary["by_bucket"].items():
        md.append(f"- {k}: {v}")

    md += ["", "## Duplicates, versions & conflicts", "",
           f"- Exact duplicates: **{summary['n_duplicates']}** (kept once, copies preserved in Historical)",
           f"- Superseded versions moved to Historical: **{summary['n_superseded']}**",
           f"- Version conflicts needing a human: **{summary['n_version_conflicts']}**"]

    md += ["", "## Needs human review", "",
           f"Total flagged: **{summary['n_needs_review']}** "
           f"(company-unresolved: {summary['n_company_unresolved']})", ""]
    for r in summary["needs_review"]:
        md.append(f"- `{r['file_id']}` {r['company']} / {r['file']} — "
                  f"{', '.join(r['flags']) or 'low confidence'} (conf {r['confidence']})")

    if summary["missing_expected"]:
        md += ["", "## Expected documents missing", ""]
        for company, miss in summary["missing_expected"].items():
            md.append(f"- **{company}**: {', '.join(miss)}")

    (reports_dir / "validation.md").write_text("\n".join(md), encoding="utf-8")

    import json
    (reports_dir / "validation.json").write_text(
        json.dumps({"summary": summary, "reconciliation": recon}, indent=2, default=str),
        encoding="utf-8")

    return {"summary": summary, "reconciliation": recon}
