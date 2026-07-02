import os, sys
from pathlib import Path
import pandas as pd
from dataroom_organizer import config as C
from dataroom_organizer.organize import _dest_bucket
from collections import Counter

DRIVE = "/Users/jackzawadzki/Library/CloudStorage/GoogleDrive-jackz@voloearth.com/Shared drives/Portfolio Company Information"


def before(src):
    out, tot = [], 0
    for e in sorted(os.scandir(src), key=lambda e: e.name.lower()):
        if e.name.startswith('.'):
            continue
        if e.is_dir():
            n = sum(1 for _ in Path(e.path).rglob('*') if _.is_file())
            tot += n
            out.append(f"  {n:>5}  {e.name}/")
        else:
            tot += 1
            out.append(f"      1  {e.name}")
    return "\n".join(out), tot


def after(plan_dir, co):
    df = pd.read_csv(Path(plan_dir) / "_CATALOG" / "catalog.csv").fillna('')
    cnt = Counter()
    for _, r in df.iterrows():
        rec = type('R', (), {'disposition': r['disposition'],
                             'bucket_code': str(r['bucket_code']).zfill(2)})
        cnt[_dest_bucket(rec)] += 1
    lines = []
    for b in C.BUCKETS:
        n = cnt.get(b.code, 0)
        note = ""
        if b.code == '14':
            note = "  (older versions, kept not deleted)"
        elif b.code == '15':
            note = "  (needs a human)"
        lines.append(f"  {n:>5}  {b.code} {b.name}{note}")
    return "\n".join(lines)


doc = ["# VoLo Data Room Organizer — REAL run (Cambium & CaliCat)", "",
       "Source: the live Google Drive (read-only). Output = a **symlink plan**: the exact",
       "end-state folder tree built from symlinks into the originals — no bytes copied, no",
       "downloads, the Drive never modified.", ""]
for co, plan in [("Cambium", "out_real/Cambium_plan"), ("CaliCat (H2U)", "out_real/CaliCat_plan")]:
    b, tot = before(Path(DRIVE) / co)
    res = Path(plan + "/_REPORTS/validation.md").read_text().split("### Result:")[1].splitlines()[0].strip()
    doc += [f"## {co}", "",
            f"### BEFORE — organized by round/event, nested up to 12 levels ({tot} files)", "",
            "```", b, "```", "",
            "### AFTER — the standard 16 folders (symlinks)", "",
            "```", after(plan, co), "```", "",
            f"Reconciliation: **{res}**", ""]
Path("BEFORE_AFTER_REAL.md").write_text("\n".join(doc), encoding="utf-8")
print("wrote BEFORE_AFTER_REAL.md", len("\n".join(doc)), "chars")
