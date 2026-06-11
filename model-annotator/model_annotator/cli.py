"""annotate-model CLI.

Exit codes: 0 success · 2 parse failure · 3 tie-outs failed catastrophically
(outputs are still written in that case).
"""
from __future__ import annotations

import argparse
import logging
import sys

from .ingest import ModelParseError
from .schema import TieOutStatus


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="annotate-model",
        description="Automated financial-model diligence: maps, verifies, derives, and annotates "
                    "a startup Excel model the way a senior analyst does by hand.",
    )
    p.add_argument("path", help="path to a .xlsx or .xlsm model (read-only)")
    p.add_argument("--out", default=None, metavar="DIR",
                   help="output directory (default ./annotations/<model>-<timestamp>/)")
    p.add_argument("--json-only", action="store_true", help="skip the markdown report")
    p.add_argument("--no-llm", action="store_true", help="heuristics-only mode, no API calls")
    p.add_argument("--benchmarks", default=None, metavar="FILE",
                   help="override the default benchmarks.yaml")
    p.add_argument("--max-cells", type=int, default=2_000_000, metavar="N",
                   help="safety cap on cells read (default 2,000,000)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    from . import annotate

    try:
        report = annotate(
            args.path,
            out_dir=args.out,
            no_llm=args.no_llm,
            benchmarks_path=args.benchmarks,
            max_cells=args.max_cells,
            json_only=args.json_only,
        )
    except ModelParseError as exc:
        print(f"annotate-model: parse failure: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"annotate-model: {exc}", file=sys.stderr)
        return 2

    n_by_sev: dict[str, int] = {}
    for f in report.findings:
        n_by_sev[f.severity.value] = n_by_sev.get(f.severity.value, 0) + 1
    sev_s = ", ".join(f"{k}: {v}" for k, v in n_by_sev.items()) or "none"
    print(f"trust score {report.workbook_map.trust_score:.2f} | findings — {sev_s} | "
          f"{len(report.acquittals)} acquittal(s), {len(report.clean_checks)} clean check(s)")

    cash_catastrophe = any(
        t.id in ("cash_roll", "beginning_cash_continuity") and t.status == TieOutStatus.failed and t.material
        for t in report.tie_outs
    )
    return 3 if cash_catastrophe else 0


if __name__ == "__main__":
    sys.exit(main())
