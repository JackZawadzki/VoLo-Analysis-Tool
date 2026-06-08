"""Tests for the reworked Portfolio Review (Drive-first) logic.

Covers the pure/unit-level pieces without needing Drive or the LLM:
folder classification, derisking scoring, attention flags, the quarter period,
LLM JSON parsing, the batched/resumable ingest loop, and the Excel loader's
match-by-name + graceful no-op behaviour.
"""

import datetime
import sqlite3

import pytest

from app.portfolio_review.schema import apply_schema
from app.portfolio_review import ingest as ing
from app.portfolio_review import derisking as dr
from app.portfolio_review import portfolio_update as pu


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


# ── Folder classifier (name heuristic / LLM fallback) ─────────────────────────
def test_is_company_folder():
    assert ing.is_company_folder("Banyan")
    assert ing.is_company_folder("BlueDot Photonics")
    assert ing.is_company_folder("CaliCat (H2U)")
    assert not ing.is_company_folder("_Cap Tables")
    assert not ing.is_company_folder("_Financials")
    assert not ing.is_company_folder("__Template Folder - Co. Name (KTF dummy)")
    assert not ing.is_company_folder("")


def test_classify_falls_back_to_heuristic_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = ing.classify_company_folders(["Banyan", "_Cap Tables", ""])
    assert res == {"Banyan": True, "_Cap Tables": False}


# ── Derisking scoring + quartiles ─────────────────────────────────────────────
def test_quartile_thresholds():
    assert [dr.compute_quartile(t) for t in (7, 5, 4, 3, 2, 1, 0, -7)] == [4, 4, 3, 3, 2, 2, 1, 1]


def test_score_company_total_and_exit():
    all_plus = {k: 1 for k in dr.DIMENSION_KEYS}
    r = dr.score_company(all_plus)
    assert r["total_score"] == 7 and r["quartile"] == 4
    assert dr.score_company(all_plus, is_exited=True)["total_score"] == 0


# ── Quarter period (so derisking trends accumulate) ───────────────────────────
def test_current_quarter(monkeypatch):
    class FakeDate:
        @staticmethod
        def today():
            return datetime.date(2026, 6, 8)
    monkeypatch.setattr(pu, "date", FakeDate)
    assert pu._current_quarter() == "Q2 2026"


# ── LLM JSON parsing ──────────────────────────────────────────────────────────
def test_parse_json_handles_fences_and_noise():
    assert pu._parse_json('{"a": 1}') == {"a": 1}
    assert pu._parse_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert pu._parse_json('here you go: {"a": 3} thanks')["a"] == 3


# ── Attention flags ───────────────────────────────────────────────────────────
def test_attention_flags():
    from app.portfolio_review.routes import _pr_company_flags
    today = "2026-06-08"

    short_runway = _pr_company_flags({}, {"runway_months": 3}, {}, {}, today)
    assert {f["kind"] for f in short_runway} == {"runway"}
    assert short_runway[0]["level"] == "alert"

    risky = _pr_company_flags({}, {"revenue_growth_pct": -0.2}, {"quartile": 1}, {}, today)
    assert {"revenue", "derisk"} <= {f["kind"] for f in risky}

    raising = _pr_company_flags({}, {"fundraising_status": "Raising Series B"}, {}, {}, today)
    assert any(f["kind"] == "fundraise" for f in raising)

    healthy = _pr_company_flags({}, {"runway_months": 24}, {"quartile": 4}, {}, today)
    assert healthy == []


# ── Batched / resumable ingestion ─────────────────────────────────────────────
def _seed_company_with_folder(conn):
    cid = conn.execute("INSERT INTO pr_companies (name, fund) VALUES ('Co','Fund I')").lastrowid
    conn.execute("INSERT INTO pr_company_folders (company_id, folder_type, drive_folder_id, "
                 "drive_folder_name) VALUES (?, 'current', 'fid', 'Co')", (cid,))
    conn.commit()
    return cid


def test_ingest_is_batched_and_resumable(conn, monkeypatch):
    cid = _seed_company_with_folder(conn)
    files = [{"id": f"f{i}", "name": f"doc{i}.pdf", "mimeType": "application/pdf",
              "modifiedTime": f"2026-01-{i + 1:02d}T00:00:00Z", "webViewLink": ""} for i in range(5)]
    monkeypatch.setattr(ing, "_list_files_recursive", lambda svc, fid: files)
    monkeypatch.setattr(ing, "_download_and_extract_text", lambda svc, fmeta: "text " + fmeta["id"])
    monkeypatch.setattr(ing, "_classify_file", lambda fmeta: "other")
    monkeypatch.setattr(ing, "mirror_granola_to_documents", lambda conn, cid: 0)

    # time budget 0 -> at least one processed, not done, has remaining
    r1 = ing.ingest_company(conn, None, cid, time_budget_s=0)
    assert r1["documents_upserted"] >= 1 and r1["remaining"] >= 1 and r1["done"] is False

    # generous budget -> finishes the rest
    r2 = ing.ingest_company(conn, None, cid, time_budget_s=999)
    assert r2["done"] is True
    assert conn.execute("SELECT COUNT(*) FROM pr_documents WHERE company_id=?", (cid,)).fetchone()[0] == 5

    # re-run -> everything already pulled (skipped), nothing to do
    r3 = ing.ingest_company(conn, None, cid, time_budget_s=999)
    assert r3["needed"] == 0 and r3["done"] is True and r3["documents_upserted"] == 0


# ── Excel loader: match-by-name + graceful no-op ──────────────────────────────
def test_loader_upsert_matches_by_name_no_dupes(conn):
    from app.portfolio_review.loader import _upsert_company
    cid = conn.execute("INSERT INTO pr_companies (name, fund) VALUES ('Banyan','Fund I')").lastrowid
    conn.commit()
    assert _upsert_company(conn, "Banyan") == cid  # matches the Drive-sourced row
    assert conn.execute("SELECT COUNT(*) FROM pr_companies WHERE name='Banyan'").fetchone()[0] == 1


def test_run_import_no_recognized_sheets_is_noop(conn, tmp_path):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook
    from app.portfolio_review.loader import run_import
    wb = Workbook()
    wb.active.title = "RandomSheet"
    wb.active["A1"] = "nothing useful"
    path = tmp_path / "working_doc.xlsx"
    wb.save(path)

    r = run_import(path, conn)
    assert r["status"] in ("success", "partial")
    assert sum(r["counts"].values()) == 0  # unrecognized workbook -> no rows, no crash
