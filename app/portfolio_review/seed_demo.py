"""
Seed a realistic LOCAL demo for the reworked Portfolio view.

Since Drive/Granola credentials aren't available locally, this stands in for
the ingestion: it creates the company roster (as if discovered from the Drive
folder) and plants a per-company document corpus in pr_documents (board decks,
investor updates, meeting notes) with enough signal for the REAL two-pass LLM
to read. It also seeds a human FY2025 derisking baseline so the AI-generated
'2026 LLM' score shows up as a trend.

Run:  python -m app.portfolio_review.seed_demo
"""

from __future__ import annotations

from ..database import get_db
from ..auth import generate_password_hash
from .schema import apply_schema
from .derisking import score_company, DIMENSION_KEYS
from .ingest import _content_hash, _est_tokens


COMPANIES = [
    dict(name="Type One Energy", fund="Fund I", sector="Fusion Energy",
         commercial_status="Pilot", fume_date="2026-11-30", next_round_expect=""),
    dict(name="Plain", fund="Fund I", sector="Utility Solar Software",
         commercial_status="Commercial", fume_date="2027-06-30", next_round_expect=""),
    dict(name="Sublime Systems", fund="Fund I", sector="Low-carbon Cement",
         commercial_status="Commercial", fume_date="2027-09-30", next_round_expect=""),
    dict(name="Mitra Chem", fund="Fund II", sector="Battery Materials",
         commercial_status="Pilot", fume_date="2026-08-31", next_round_expect=""),
]

# Human FY2025 baseline derisking (7 dims, in DIMENSION_KEYS order)
BASELINE = {
    "Type One Energy": [0, 0, 1, 1, 1, 0, 1],
    "Plain":           [1, 1, 0, 1, 1, 1, 0],
    "Sublime Systems": [1, 1, 1, 1, 1, 1, 0],
    "Mitra Chem":      [-1, 0, 0, -1, 0, -1, 0],
}

BOARD = {
    "Type One Energy": [("Director", "VoLo Earth (J. Powell)")],
    "Plain":           [("Observer", "VoLo Earth")],
    "Sublime Systems": [("Director", "VoLo Earth")],
}
# (as_of, cost, fmv, multiple)
RETURNS = {
    "Type One Energy": ("2026-03-31", 3_000_000, 6_600_000, 2.20),
    "Plain":           ("2026-03-31", 4_000_000, 5_000_000, 1.25),
    "Sublime Systems": ("2026-03-31", 2_500_000, 7_500_000, 3.00),
    "Mitra Chem":      ("2026-03-31", 3_500_000, 3_000_000, 0.86),
}

# Per-company document corpus: (title, doc_type, source, occurred_at, body)
DOCS = {
    "Type One Energy": [
        ("Q1 2026 Board Deck", "board", "drive", "2026-03-20",
         "Type One Energy — Q1 2026 board update. Status: pre-revenue, first-of-a-kind "
         "stellarator pilot under construction at the TVA Bull Run site. Technical milestone: "
         "completed Infinity One magnet test campaign at full field; high-temperature "
         "superconducting coils performed within 3% of model. Headcount 142 (up from 110). "
         "Cash on hand $48M; monthly burn $6.0M implies ~8 months runway. Plan: close Series B "
         "of $250M in H2 2026 to fund the demonstration machine. Key risks flagged by the board: "
         "fusion regulatory pathway (NRC vs state) still unsettled; supply of HTS tape concentrated "
         "in two vendors. Team: added VP Manufacturing from SpaceX. No revenue yet; first power "
         "purchase LOIs under discussion with two utilities."),
        ("Investor Update — Feb 2026", "investor", "drive", "2026-02-15",
         "Investor letter. We are beginning to raise our Series B (~$250M). Ask: intros to "
         "strategic energy LPs and sovereign funds. Progress: magnet milestone de-risks the "
         "hardest physics question. Hiring ramp on plan. Watch items: runway tightens to ~8 months; "
         "Series B timing is the single biggest risk. IP: 14 patents filed on magnet architecture."),
        ("Granola — Powell / CEO sync", "note", "granola", "2026-03-05",
         "Call with CEO. Confident on magnet results. Worried about Series B market for hardware-"
         "heavy fusion; may need a bridge if the round slips past Q3. Asked VoLo to help with "
         "DOE loan-guarantee introductions. Team morale strong post-milestone."),
    ],
    "Plain": [
        ("Q1 2026 Board Deck", "board", "drive", "2026-03-18",
         "Plain — Q1 2026 board deck. Commercial SaaS for utility-scale solar EPC management. "
         "ARR $4.2M, up from $3.0M a year ago (+40% YoY). 38 paying customers (up from 26), net "
         "revenue retention 121%. Gross margin 78%. Cash $14M, burn $0.7M/mo, runway ~20 months. "
         "Not currently raising. Milestone: signed two top-5 IPP logos; launched automated "
         "interconnection module. Risks: lengthening sales cycles; one competitor well-funded. "
         "Team stable, hired VP Sales."),
        ("Investor Update — Mar 2026", "investor", "drive", "2026-03-01",
         "Strong quarter. Revenue ahead of plan, NRR healthy. No raise planned for 12+ months. "
         "Asks: customer intros to IPPs in ERCOT. Product roadmap on track."),
        ("Granola — Plain QBR", "note", "granola", "2026-02-20",
         "QBR. Pipeline robust. Management focused on enterprise expansion. No cash concerns. "
         "Considering a small tuck-in acquisition."),
    ],
    "Sublime Systems": [
        ("Q1 2026 Board Deck", "board", "drive", "2026-03-22",
         "Sublime Systems — Q1 2026. Electrochemical low-carbon cement. Commercial: first commercial "
         "plant (Holyoke) commissioning, nameplate 30k tpy. Revenue $1.1M (early commercial), up from "
         "$0.2M. Signed offtake MOUs with Microsoft and a major developer; Holcim partnership expanded. "
         "Cash $60M post Series B; burn $2.5M/mo, runway ~24 months. Milestone: ASTM certification of "
         "ordinary-portland-equivalent product. Risks: scale-up capex; cost-down curve vs incumbent "
         "Portland cement. Strong technical and policy team (IRA 45Q tailwinds)."),
        ("Investor Update — Feb 2026", "investor", "drive", "2026-02-10",
         "Plant commissioning on schedule. Offtake demand exceeds 2027 capacity. Considering a growth "
         "round in 2027 to fund plant #2. No near-term cash needs."),
        ("Granola — Sublime board prep", "note", "granola", "2026-03-10",
         "Board prep. Microsoft offtake firming. Capex for plant #2 is the gating item. CEO strong; "
         "discussing project-finance structures to avoid dilution."),
    ],
    "Mitra Chem": [
        ("Q1 2026 Board Deck", "board", "drive", "2026-03-15",
         "Mitra Chem — Q1 2026 board deck. LFP cathode materials. Status: pilot; pre-commercial. "
         "Revenue $0.6M, DOWN from $0.9M last year (qualification samples slipped). One pilot customer "
         "paused orders. Cash $4.2M, burn $1.4M/mo, runway ~3 months — URGENT. Raising a Series B "
         "bridge now; lead not yet committed. Milestone: AI-guided materials platform shipped two new "
         "formulations, but GM qualification timeline pushed to 2027. Risks (board-flagged): cash crisis; "
         "CFO departed in Jan and not yet backfilled; GM partnership at risk if qualification slips again; "
         "single-site manufacturing. Team turnover a concern."),
        ("Investor Update — Mar 2026", "investor", "drive", "2026-03-02",
         "Candid update: we are tight on cash and raising a bridge urgently. Ask: bridge participation "
         "and intros to strategic battery investors. GM qualification delayed; revenue softer than plan. "
         "Working to stabilize the team after CFO departure."),
        ("Granola — Mitra urgent call", "note", "granola", "2026-03-08",
         "Emergency call. ~3 months of runway. Bridge needed by April. CEO seeking strategic capital; "
         "GM relationship strained by delays. Discussed interim CFO candidates. High risk."),
    ],
}


def _get_or_create_company(conn, c: dict) -> int:
    row = conn.execute("SELECT id FROM pr_companies WHERE name=?", (c["name"],)).fetchone()
    if row:
        cid = row["id"]
        conn.execute(
            "UPDATE pr_companies SET fund=?, sector=?, commercial_status=?, fume_date=?, "
            "next_round_expect=?, updated_at=datetime('now') WHERE id=?",
            (c["fund"], c["sector"], c["commercial_status"], c["fume_date"],
             c["next_round_expect"], cid))
        return cid
    cur = conn.execute(
        "INSERT INTO pr_companies (name, fund, sector, commercial_status, fume_date, next_round_expect) "
        "VALUES (?,?,?,?,?,?)",
        (c["name"], c["fund"], c["sector"], c["commercial_status"], c["fume_date"], c["next_round_expect"]))
    return cur.lastrowid


def seed(conn) -> dict:
    apply_schema(conn)
    counts = {"companies": 0, "documents": 0, "derisking": 0, "board": 0, "returns": 0}
    for c in COMPANIES:
        cid = _get_or_create_company(conn, c)
        counts["companies"] += 1
        for tbl in ("pr_documents", "pr_derisking_scores", "pr_traction_snapshots",
                    "pr_company_updates", "pr_board_seats", "pr_returns"):
            conn.execute(f"DELETE FROM {tbl} WHERE company_id=?", (cid,))

        for i, (title, dtype, source, occurred, body) in enumerate(DOCS.get(c["name"], [])):
            conn.execute(
                """INSERT INTO pr_documents (company_id, source, source_doc_id, title, doc_type,
                   mime_type, source_url, folder_path, body_text, body_tokens, content_hash, occurred_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, source, f"seed-{cid}-{i}", title, dtype, "text/plain", "",
                 c["name"], body, _est_tokens(body), _content_hash(body), occurred))
            counts["documents"] += 1

        dims = BASELINE.get(c["name"])
        if dims:
            res = score_company(dict(zip(DIMENSION_KEYS, dims)))
            conn.execute(
                f"""INSERT INTO pr_derisking_scores (company_id, period, fund,
                    {', '.join(DIMENSION_KEYS)}, is_exited, total_score, quartile, evaluator)
                    VALUES (?,?,?,{','.join(['?'] * 7)},?,?,?,?)""",
                (cid, "FY2025", c["fund"], *dims, 0, res["total_score"], res["quartile"], "human"))
            counts["derisking"] += 1

        for seat_type, member in BOARD.get(c["name"], []):
            conn.execute("INSERT INTO pr_board_seats (company_id, seat_type, board_member, active) "
                         "VALUES (?,?,?,1)", (cid, seat_type, member))
            counts["board"] += 1

        r = RETURNS.get(c["name"])
        if r:
            as_of, cost, fmv, m = r
            conn.execute(
                "INSERT INTO pr_returns (company_id, as_of_date, cost, fmv, total_value, multiple) "
                "VALUES (?,?,?,?,?,?)", (cid, as_of, cost, fmv, fmv, m))
            counts["returns"] += 1

    conn.commit()
    return counts


def reset_preview_login(conn, username: str = "preview", password: str = "Preview1234!") -> bool:
    row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        return False
    conn.execute("UPDATE users SET password_hash=?, verified=1 WHERE id=?",
                 (generate_password_hash(password), row["id"]))
    conn.commit()
    return True


def main():
    conn = get_db()
    try:
        counts = seed(conn)
        ok = reset_preview_login(conn)
        print("[seed_demo] inserted:", counts)
        print("[seed_demo] preview login reset:", ok, "(preview / Preview1234!)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
