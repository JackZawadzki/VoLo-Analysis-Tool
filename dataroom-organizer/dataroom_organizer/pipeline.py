"""
End-to-end orchestration of the three phases, with the catalog as the hub.

  Phase 1  inventory()  -- read-only crawl + classify + dedup + catalog.
  Phase 2  copy + verify the drive.
  Phase 3  reorganize the copy, generate navigation guides + validation reports.

`inventory()` stops after Phase 1 (the plan's first sign-off gate); `run()` does
all three. The original drive is only ever read.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from . import catalog, crawl, dedup, navigate, organize, validate
from .classify import LLMHook


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def inventory(source_root: Path, out_root: Path, *,
              companies: Optional[list[str]] = None,
              llm_hook: Optional[LLMHook] = None,
              single_company: Optional[str] = None,
              content_mode: str = "full", full_hash: bool = True) -> dict:
    """Phase 1 only -- read-only. Builds the catalog; changes nothing on disk."""
    source_root = Path(source_root).resolve()
    out_root = Path(out_root)
    organize.assert_safe_output(source_root, out_root)
    records = crawl.crawl(source_root, companies=companies, llm_hook=llm_hook,
                          single_company=single_company,
                          content_mode=content_mode, full_hash=full_hash)
    dd = dedup.resolve(records)
    cat_dir = out_root / "_CATALOG"
    paths = catalog.write(records, cat_dir)
    summary = validate.summarize(records)
    (out_root / "_REPORTS").mkdir(parents=True, exist_ok=True)
    (out_root / "_REPORTS" / "phase1_summary.json").write_text(
        json.dumps({"dedup": dd, "summary": summary, "catalog": paths,
                    "generated": _now()}, indent=2, default=str), encoding="utf-8")
    return {"records": records, "dedup": dd, "summary": summary, "catalog": paths}


def plan(source_root: Path, out_root: Path, *,
         companies: Optional[list[str]] = None,
         llm_hook: Optional[LLMHook] = None,
         single_company: Optional[str] = None,
         content_mode: str = "fast") -> dict:
    """Build the full reorganized structure as SYMLINKS into the originals.

    Shows the exact end-state folder tree, navigation guides, catalog and
    reconciliation WITHOUT copying bytes -- ideal for a live, partially-streamed
    Google Drive where a real byte copy would force gigabytes of downloads. The
    original is never touched (symlinks only point at it)."""
    source_root = Path(source_root).resolve()
    out_root = Path(out_root)
    organize.assert_safe_output(source_root, out_root)
    if out_root.exists():
        import shutil
        shutil.rmtree(out_root)

    records = crawl.crawl(source_root, companies=companies, llm_hook=llm_hook,
                          single_company=single_company, content_mode=content_mode,
                          full_hash=False)
    dd = dedup.resolve(records)
    reorg = organize.reorganize(records, out_root, link=True)
    n_guides = navigate.write_all(records, out_root)
    paths = catalog.write(records, out_root / "_CATALOG")
    reports = validate.write_reports(records, source_root, out_root,
                                     out_root / "_REPORTS", verify_hash=False)

    logs_dir = out_root / "_LOGS"
    logs_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(reorg["move_log"]).to_csv(logs_dir / "placement_manifest.csv", index=False)
    (logs_dir / "dedup.json").write_text(json.dumps(dd, indent=2, default=str), encoding="utf-8")

    return {"records": records, "generated": _now(), "mode": "plan-symlinks",
            "source": str(source_root), "out": str(out_root),
            "phase3": {k: v for k, v in reorg.items() if k != "move_log"},
            "n_navigation_guides": n_guides, "catalog": paths,
            "reconciliation": reports["reconciliation"], "summary": reports["summary"]}


def run(source_root: Path, out_root: Path, *,
        companies: Optional[list[str]] = None,
        llm_hook: Optional[LLMHook] = None,
        single_company: Optional[str] = None,
        content_mode: str = "full") -> dict:
    """All three phases. `out_root` becomes the clean, reorganized copy of the drive."""
    source_root = Path(source_root).resolve()
    out_root = Path(out_root)
    organize.assert_safe_output(source_root, out_root)

    # Phase 1 — read-only crawl + classify + dedup (full hashes for reconciliation)
    records = crawl.crawl(source_root, companies=companies, llm_hook=llm_hook,
                          single_company=single_company, content_mode=content_mode,
                          full_hash=True)
    dd = dedup.resolve(records)

    # Phase 2 — copy the drive and verify it is faithful
    verify = organize.copy_and_verify(source_root, out_root)

    # Phase 3 — reorganize the copy, in place
    reorg = organize.reorganize(records, out_root)

    # navigation guides + catalog + validation reports (all generated from the catalog)
    n_guides = navigate.write_all(records, out_root)
    paths = catalog.write(records, out_root / "_CATALOG")
    reports = validate.write_reports(records, source_root, out_root, out_root / "_REPORTS")

    # logs
    logs_dir = out_root / "_LOGS"
    logs_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(reorg["move_log"]).to_csv(logs_dir / "move_log.csv", index=False)
    (logs_dir / "copy_verify.json").write_text(json.dumps(verify, indent=2), encoding="utf-8")
    (logs_dir / "dedup.json").write_text(json.dumps(dd, indent=2, default=str), encoding="utf-8")

    run_summary = {
        "generated": _now(),
        "source": str(source_root),
        "out": str(out_root),
        "phase2_copy_verify": verify,
        "phase3_reorganize": {k: v for k, v in reorg.items() if k != "move_log"},
        "n_navigation_guides": n_guides,
        "catalog": paths,
        "reconciliation": reports["reconciliation"],
        "summary": reports["summary"],
    }
    (logs_dir / "run_summary.json").write_text(
        json.dumps(run_summary, indent=2, default=str), encoding="utf-8")

    return {"records": records, **run_summary}
