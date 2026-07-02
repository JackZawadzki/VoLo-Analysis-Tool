"""Command-line entry point.

    python -m dataroom_organizer inventory --source DRIVE --out OUT   # Phase 1 (read-only)
    python -m dataroom_organizer run       --source DRIVE --out OUT   # Phases 1-3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import pipeline


def _print_phase1(res: dict) -> None:
    s, dd = res["summary"], res["dedup"]
    print(f"  companies         : {s['n_companies']}")
    print(f"  files catalogued  : {dd['n_records']}")
    print(f"  placed/superseded/dup/pending : "
          f"{dd['n_placed']}/{dd['n_superseded']}/{dd['n_duplicates']}/{dd['n_pending']}")
    print(f"  avg confidence    : {s['avg_confidence_placed']}")
    print(f"  needs human review: {s['n_needs_review']}")
    print(f"  catalog           : {res['catalog']['xlsx'] or res['catalog']['csv']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dataroom_organizer",
                                 description="VoLo data-room reorganizer (catalog-driven, reversible)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("inventory", "plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--source", required=True, help="path to the (original) drive to read")
        p.add_argument("--out", required=True, help="output path for catalog / reorganized copy")
        p.add_argument("--company", action="append", default=[],
                       help="seed a company name (repeatable)")
        p.add_argument("--single-company", default=None,
                       help="treat --source as one company's folder (this is the company)")
        if name == "inventory":
            p.add_argument("--light", action="store_true",
                           help="fast read-only preview: metadata-only, no content reads or hashes")
        else:
            p.add_argument("--content", choices=["full", "fast", "none"],
                           default="fast" if name == "plan" else "full",
                           help="content depth: full=all, fast=spreadsheets/text only, none=names+paths")

    args = ap.parse_args(argv)
    source = Path(args.source)
    if not source.exists():
        print(f"error: source not found: {source}", file=sys.stderr)
        return 2

    if args.cmd == "inventory":
        mode = "light/read-only" if args.light else "read-only"
        print(f"Phase 1 ({mode}) — cataloguing {source} …")
        res = pipeline.inventory(source, Path(args.out), companies=args.company or None,
                                 single_company=args.single_company,
                                 content_mode="none" if args.light else "full",
                                 full_hash=not args.light)
        _print_phase1(res)
        print("\nReview the catalog, then run `plan` (symlinks) or `run` (real copy).")
        return 0

    if args.cmd == "plan":
        print(f"Plan (symlinks, no copy) — {source} → {args.out} (content={args.content}) …")
        res = pipeline.plan(source, Path(args.out), companies=args.company or None,
                            single_company=args.single_company, content_mode=args.content)
        recon = res["reconciliation"]
        s = res["summary"]
        print(f"  files placed (symlinks): {recon['n_placed']}/{recon['n_source_files']}")
        print(f"  dispositions           : {s['dispositions']}")
        print(f"  navigation guides      : {res['n_navigation_guides']}")
        print(f"  reconciliation         : {'PASS' if recon['pass'] else 'FAIL'} ({recon['mode']})")
        print(f"  reorganized tree       : {args.out}")
        return 0

    print(f"Phases 1–3 — {source} → {args.out} (content={args.content}) …")
    res = pipeline.run(source, Path(args.out), companies=args.company or None,
                       single_company=args.single_company, content_mode=args.content)
    recon = res["reconciliation"]
    _print_phase1({"summary": res["summary"],
                   "dedup": {"n_records": recon["n_source_files"],
                             "n_placed": res["summary"]["dispositions"].get("PLACED", 0),
                             "n_superseded": res["summary"]["dispositions"].get("SUPERSEDED", 0),
                             "n_duplicates": res["summary"]["dispositions"].get("DUPLICATE", 0),
                             "n_pending": res["summary"]["dispositions"].get("PENDING", 0)},
                   "catalog": res["catalog"]})
    print(f"  copy faithful     : {res['phase2_copy_verify']['faithful']}")
    print(f"  reconciliation    : {'PASS — nothing lost' if recon['pass'] else 'FAIL'}")
    print(f"  reorganized drive : {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
