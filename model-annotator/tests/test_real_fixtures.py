"""Real-model fixtures: anything dropped into tests/fixtures/real/ (gitignored,
confidential) is run through the full pipeline; a summary table prints so the
analyst can judge recall against their own annotations.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REAL = Path(__file__).parent / "fixtures" / "real"
MODELS = sorted(p for p in REAL.glob("*.xls[xm]") if not p.name.startswith("~$"))


@pytest.mark.parametrize("path", MODELS, ids=lambda p: p.stem[:40])
def test_real_model(path, tmp_path):
    from model_annotator import annotate

    report = annotate(str(path), out_dir=str(tmp_path / path.stem), no_llm=True)

    sev: dict[str, int] = {}
    for f in report.findings:
        sev[f.severity.value] = sev.get(f.severity.value, 0) + 1
    print(f"\n{'=' * 78}\n{path.name}")
    print(f"  trust {report.workbook_map.trust_score:.2f} | "
          f"findings {sev or '{}'} | {len(report.acquittals)} acquittals | "
          f"{len(report.clean_checks)} clean checks | "
          f"{len(report.derived_metrics)} metrics | "
          f"{len(report.limitations)} limitations")
    for f in report.sorted_findings():
        print(f"  [{f.severity.value:8s}] ({f.category}) {f.title[:80]}")

    # hard invariants on every real model: schema-valid, citations clean
    assert report.schema_version.startswith("1.")
    citation_failures = [l for l in report.limitations if l.startswith("CITATION VALIDATION FAILURE")]
    assert not citation_failures, citation_failures


def test_note_when_empty():
    if not MODELS:
        pytest.skip("no real models in tests/fixtures/real/ — drop .xlsx/.xlsm files there to run them")
