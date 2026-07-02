"""
Navigation guides -- generated from the catalog.

At the top of the drive, in each company's Overview folder, and inside every one
of the 16 folders sits a short, plain-language index (plan, Section 3). It says
what the folder is for, lists each document with a one-line description, its date,
where it came from, and whether it is current or superseded -- and, importantly,
notes what is expected but missing. A person and an AI get the same honest picture.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from . import config as C
from .organize import COMPANIES_DIR, _company_dirname, _dest_bucket
from .models import FileRecord

_KEY_FACT_LABELS = ["revenue", "arr", "mrr", "valuation", "post-money", "pre-money",
                    "fully diluted", "shares outstanding", "runway", "cash", "burn"]


def _company_records(records: list[FileRecord]) -> dict[str, list[FileRecord]]:
    out: dict[str, list[FileRecord]] = defaultdict(list)
    for r in records:
        key = r.company if r.company not in ("", "UNKNOWN") else "UNKNOWN"
        out[key].append(r)
    return out


def _bucket_records(recs: list[FileRecord]) -> dict[str, list[FileRecord]]:
    out: dict[str, list[FileRecord]] = defaultdict(list)
    for r in recs:
        out[_dest_bucket(r)].append(r)
    return out


def _doc_line(r: FileRecord) -> str:
    status = "current" if r.current and r.disposition == "PLACED" else r.disposition.lower()
    src = f"from {r.origin_folder or 'root'} ({r.origin_kind})"
    conf = f", {r.confidentiality}" if r.confidentiality else ""
    flag = f"  ⚠️ {', '.join(r.flags)}" if r.flags else ""
    name = Path(r.dest_rel).name if r.dest_rel else r.filename
    return (f"- **{name}** — {r.summary}  \n"
            f"  _{r.doc_date or 'no date'} · {status}{conf} · {src} · "
            f"conf {r.confidence:.2f}_{flag}")


def _key_facts(recs: list[FileRecord]) -> list[str]:
    facts: list[str] = []
    seen = set()
    for r in recs:
        if not r.current:
            continue
        for n in r.extracted_numbers:
            lbl = n.get("label", "").lower()
            if any(k in lbl for k in _KEY_FACT_LABELS) and lbl not in seen:
                seen.add(lbl)
                facts.append(f"- {n['label']}: **{n['value']}**  "
                             f"_(read by code from {Path(r.dest_rel or r.filename).name} · "
                             f"{n.get('ref','?')})_")
    return facts[:10]


def write_all(records: list[FileRecord], work_root: Path, *, reports_rel: str = "_REPORTS") -> int:
    n = 0
    base = work_root / COMPANIES_DIR
    by_company = _company_records(records)

    # --- top-level drive index -------------------------------------------------
    lines = ["# VoLo Data Room — Index", "",
             "_Auto-generated from the master catalog. The original drive is preserved "
             "untouched as a backup._", "",
             f"**Companies:** {len(by_company)}  ·  **Files catalogued:** {len(records)}",
             "", "## Companies", ""]
    for company in sorted(by_company):
        recs = by_company[company]
        cur = sum(1 for r in recs if r.current and r.disposition == "PLACED")
        rev = sum(1 for r in recs if r.needs_review)
        dirname = _company_dirname(company)
        lines.append(f"- **[{company}]({COMPANIES_DIR}/{dirname}/{C.BUCKETS[0].slug}/INDEX.md)** "
                     f"— {len(recs)} files, {cur} current"
                     + (f", {rev} need review" if rev else ""))
    lines += ["", "## Reports", "",
              f"- [Validation & reconciliation]({reports_rel}/validation.md)",
              f"- Master catalog: `_CATALOG/catalog.xlsx` (and .csv/.sqlite/.json)"]
    (work_root / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    n += 1

    # --- per company -----------------------------------------------------------
    for company, recs in by_company.items():
        cdir = base / _company_dirname(company)
        by_bucket = _bucket_records(recs)

        # 00 overview
        ov = [f"# {company} — Overview & Index", "",
              "_Auto-generated. This folder summarizes everything VoLo holds on the company._",
              ""]
        facts = _key_facts(recs)
        if facts:
            ov += ["## Key facts (read by code, with source)", "", *facts, ""]
        ov += ["## Folder guide", "",
               "| # | Folder | Files | Status |", "|---|---|---|---|"]
        missing_expected = []
        for b in C.BUCKETS:
            here = by_bucket.get(b.code, [])
            ncur = sum(1 for r in here if r.current)
            if b.expected and ncur == 0 and b.code not in (C.HISTORICAL_CODE, C.PENDING_CODE):
                status = "⚠️ expected but **missing**"
                missing_expected.append(f"{b.code} {b.name}")
            elif not here:
                status = "empty"
            else:
                status = f"{ncur} current"
            ov.append(f"| {b.code} | {b.name} | {len(here)} | {status} |")
        if missing_expected:
            ov += ["", "## Expected but missing", "",
                   *[f"- {m}" for m in missing_expected]]
        review = [r for r in recs if r.needs_review]
        if review:
            ov += ["", "## Flagged for human review", "",
                   *[f"- {r.filename} — {', '.join(r.flags) or 'low confidence'} "
                     f"(conf {r.confidence:.2f})" for r in review]]
        (cdir / C.BUCKETS[0].slug).mkdir(parents=True, exist_ok=True)
        (cdir / C.BUCKETS[0].slug / "INDEX.md").write_text("\n".join(ov), encoding="utf-8")
        n += 1

        # each of the 16 folders
        for b in C.BUCKETS:
            if b.code == C.OVERVIEW_CODE:
                continue
            here = sorted(by_bucket.get(b.code, []),
                          key=lambda r: (not r.current, r.doc_date), reverse=False)
            fdir = cdir / b.slug
            fdir.mkdir(parents=True, exist_ok=True)
            fl = [f"# {b.code} · {b.name}", "", f"_{b.blurb}_", ""]
            if here:
                fl += [_doc_line(r) for r in here]
            else:
                fl.append("_(empty — no documents on file)_")
            if b.expected and not any(r.current for r in here):
                fl += ["", "> ⚠️ **Expected but missing:** a current document is normally "
                       "filed here for every company."]
            (fdir / "_INDEX.md").write_text("\n".join(fl), encoding="utf-8")
            n += 1

    return n
