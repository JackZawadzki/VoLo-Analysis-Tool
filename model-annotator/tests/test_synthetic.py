"""Shared harness: every synthetic fixture is generated, annotated, and judged
against its expectations YAML. Adding a fixture = adding gen_<name>.py plus
expected/<name>.yaml; this file discovers both automatically.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
SYNTH = HERE / "fixtures" / "synthetic"
EXPECTED = HERE / "expected"

_SEV_RANK = {"info": 0, "medium": 1, "high": 2, "critical": 3}


def _generators() -> list[str]:
    return sorted(p.stem[4:] for p in SYNTH.glob("gen_*.py"))


def _load_gen(name: str):
    spec = importlib.util.spec_from_file_location(f"gen_{name}", SYNTH / f"gen_{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def reports(tmp_path_factory):
    """name -> (Report, exit_code). Built once per session."""
    from model_annotator import annotate
    from model_annotator.schema import TieOutStatus

    out: dict[str, tuple] = {}
    for name in _generators():
        mod = _load_gen(name)
        wb_path = tmp_path_factory.mktemp(name) / f"{name}.xlsx"
        mod.build(str(wb_path))
        report = annotate(str(wb_path), no_llm=True, write_outputs=False)
        cash_fail = any(
            t.id in ("cash_roll", "beginning_cash_continuity")
            and t.status == TieOutStatus.failed and t.material
            for t in report.tie_outs)
        out[name] = (report, 3 if cash_fail else 0)
    return out


def _matches(finding, rule: dict) -> bool:
    if "category" in rule and finding.category != rule["category"]:
        return False
    if rule.get("min_severity") and _SEV_RANK[finding.severity.value] < _SEV_RANK[rule["min_severity"]]:
        return False
    if rule.get("title_contains") and rule["title_contains"].lower() not in finding.title.lower():
        return False
    ev_any = rule.get("evidence_contains_any")
    if ev_any:
        blob = " ".join(finding.evidence_cells) + " " + " ".join(e.ref for e in finding.evidence_values)
        if not any(sub in blob for sub in ev_any):
            return False
    return True


@pytest.mark.parametrize("name", _generators())
def test_fixture(name, reports):
    exp_path = EXPECTED / f"{name}.yaml"
    assert exp_path.exists(), f"missing expectations file {exp_path}"
    exp = yaml.safe_load(exp_path.read_text())
    report, exit_code = reports[name]

    summary = "; ".join(f"[{f.severity.value}]{f.category}" for f in report.findings) or "(no findings)"
    acq_summary = "; ".join(a.category for a in report.acquittals) or "(none)"

    if exp.get("exit_code") is not None:
        assert exit_code == exp["exit_code"], \
            f"{name}: exit {exit_code} != {exp['exit_code']}  findings: {summary}"
    if exp.get("trust_max") is not None:
        assert report.workbook_map.trust_score <= exp["trust_max"], \
            f"{name}: trust {report.workbook_map.trust_score} > {exp['trust_max']}"
    if exp.get("trust_min") is not None:
        assert report.workbook_map.trust_score >= exp["trust_min"], \
            f"{name}: trust {report.workbook_map.trust_score} < {exp['trust_min']}"

    for rule in exp.get("findings_must_include") or []:
        assert any(_matches(f, rule) for f in report.findings), \
            f"{name}: no finding matches {rule}.  Got: {summary}"
    for rule in exp.get("findings_must_not_include") or []:
        hits = [f for f in report.findings if _matches(f, rule)]
        assert not hits, f"{name}: forbidden finding matched {rule}: {[f.title for f in hits]}"
    for rule in exp.get("acquittals_must_include") or []:
        cat = rule.get("category")
        sub = rule.get("contains", "")
        ok = any((not cat or a.category == cat) and (sub.lower() in (a.candidate + a.decomposition).lower())
                 for a in report.acquittals)
        assert ok, f"{name}: no acquittal matches {rule}.  Acquittals: {acq_summary}"

    cap = exp.get("max_high_critical")
    if cap is not None:
        n = sum(1 for f in report.findings if f.severity.value in ("high", "critical"))
        assert n <= cap, f"{name}: {n} high/critical findings exceed cap {cap}: {summary}"


def test_determinism(reports, tmp_path):
    """--no-llm reruns are byte-identical modulo the timestamp field."""
    from model_annotator import annotate
    names = _generators()
    if not names:
        pytest.skip("no fixtures yet")
    name = names[0]
    mod = _load_gen(name)
    wb = tmp_path / f"{name}.xlsx"
    mod.build(str(wb))
    r1 = annotate(str(wb), no_llm=True, write_outputs=False)
    r2 = annotate(str(wb), no_llm=True, write_outputs=False)
    d1, d2 = r1.model_dump(), r2.model_dump()
    d1.pop("analyzed_at"), d2.pop("analyzed_at")
    assert d1 == d2
