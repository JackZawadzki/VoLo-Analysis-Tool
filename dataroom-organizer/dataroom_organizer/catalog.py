"""
The master catalog -- the single source of truth.

Persists the list of FileRecords to several interchangeable forms so a person can
open it in a spreadsheet and code can query it: CSV, XLSX, SQLite, and JSON. Every
other artifact (clean folders, navigation guides, validation reports) is generated
from this catalog (plan, Guiding Principles).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from .models import FileRecord

COLUMNS = [
    "file_id", "company", "bucket_code", "bucket_name", "disposition", "current",
    "confidence", "needs_review", "doc_date", "doc_date_source", "confidentiality",
    "stage", "version_family", "superseded_by", "duplicate_of", "filename", "ext",
    "size_bytes", "mtime_iso", "sha256", "origin_folder", "origin_kind",
    "company_evidence", "summary", "extracted_numbers", "evidence", "flags",
    "gnative_url", "also_relevant_to", "source_rel", "dest_rel",
]


def to_dataframe(records: list[FileRecord]) -> pd.DataFrame:
    rows = [r.to_row() for r in records]
    df = pd.DataFrame(rows)
    # keep a stable, human-friendly column order
    ordered = [c for c in COLUMNS if c in df.columns]
    rest = [c for c in df.columns if c not in ordered]
    return df[ordered + rest]


def write(records: list[FileRecord], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    df = to_dataframe(records)

    csv_path = out_dir / "catalog.csv"
    xlsx_path = out_dir / "catalog.xlsx"
    db_path = out_dir / "catalog.sqlite"
    json_path = out_dir / "catalog.json"

    df.to_csv(csv_path, index=False)
    try:
        df.to_excel(xlsx_path, index=False, sheet_name="catalog")
    except Exception:
        xlsx_path = None  # type: ignore

    con = sqlite3.connect(db_path)
    try:
        df.to_sql("catalog", con, if_exists="replace", index=False)
    finally:
        con.close()

    json_path.write_text(
        json.dumps([r.to_row() for r in records], indent=2, default=str),
        encoding="utf-8",
    )

    return {
        "csv": str(csv_path),
        "xlsx": str(xlsx_path) if xlsx_path else "",
        "sqlite": str(db_path),
        "json": str(json_path),
    }
