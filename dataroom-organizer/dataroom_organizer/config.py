"""
Taxonomy and classification configuration for the VoLo data-room reorganizer.

This module is the single declarative source for:
  * the standard 16-folder company structure (the plan's Section 2c / Section 3),
  * how legacy cross-cutting "theme" folders fold back into a company's folder,
  * the deterministic, simplest-baseline-first classification rules
    (filename / origin / content / extension signals with weights),
  * confidentiality + version token vocabularies.

Everything downstream (catalog, folder build, navigation guides, validation)
is generated from the catalog, and the catalog is produced using the rules here.
The 16-folder set is provisional until VoLo's partners weigh in (plan, Guiding
Principles) -- so it lives in one editable place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# The standard 16 folders. `code` is the numeric prefix; `slug` is the on-disk
# directory name; `name` is the human label used in navigation guides.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Bucket:
    code: str
    name: str
    slug: str
    blurb: str
    # Whether a company is normally expected to have at least one doc here.
    expected: bool = False


BUCKETS: list[Bucket] = [
    Bucket("00", "Overview & Index", "00_Overview_and_Index",
           "An auto-generated company summary, key facts, and the folder guide."),
    Bucket("01", "Investment Memos & IC", "01_Investment_Memos_and_IC",
           "Investment memos (all versions tagged), screening notes, IC materials.",
           expected=True),
    Bucket("02", "Financing & Legal", "02_Financing_and_Legal",
           "Term sheets, purchase agreements, SAFEs and notes, board consents, "
           "corporate docs, D&O insurance, org chart.",
           expected=True),
    Bucket("03", "Cap Table & Equity", "03_Cap_Table_and_Equity",
           "Cap tables, securities ledgers, employee compensation and option plans.",
           expected=True),
    Bucket("04", "Financials & Models", "04_Financials_and_Models",
           "Financial statements, financial models, budgets and projections.",
           expected=True),
    Bucket("05", "Non-Dilutive & Grants", "05_Non_Dilutive_and_Grants",
           "Grant awards and other non-dilutive funding."),
    Bucket("06", "Board & Governance", "06_Board_and_Governance",
           "Board decks, minutes, and resolutions."),
    Bucket("07", "Monitoring & Investor Updates", "07_Monitoring_and_Investor_Updates",
           "Ongoing KPI and monitoring data, and company investor updates."),
    Bucket("08", "Meeting Notes & Calls", "08_Meeting_Notes_and_Calls",
           "Notes from calls and meetings (and, later, Granola transcripts)."),
    Bucket("09", "Commercial & Customers", "09_Commercial_and_Customers",
           "Customer contracts, commercial pipeline, references, LOIs."),
    Bucket("10", "Technical & Diligence", "10_Technical_and_Diligence",
           "Technical diligence, IP and patents, data received from the company."),
    Bucket("11", "Impact & Climate", "11_Impact_and_Climate",
           "Impact reporting and carbon or energy metrics."),
    Bucket("12", "Company Materials", "12_Company_Materials",
           "Decks, one-pagers, and product materials the company provides."),
    Bucket("13", "Third-Party & Research", "13_Third_Party_and_Research",
           "Outside research, market reports, 409A valuations."),
    Bucket("14", "Historical / Superseded", "14_Historical_Superseded",
           "Old and replaced versions, kept rather than deleted."),
    Bucket("15", "Unclassified / Pending Review", "15_Unclassified_Pending_Review",
           "Anything uncertain, waiting for a human decision."),
]

BUCKET_BY_CODE: dict[str, Bucket] = {b.code: b for b in BUCKETS}
OVERVIEW_CODE = "00"
HISTORICAL_CODE = "14"
PENDING_CODE = "15"
# Codes a document can actually be *classified into* (00/14/15 are derived, not chosen).
CLASSIFIABLE_CODES = [b.code for b in BUCKETS if b.code not in (OVERVIEW_CODE, HISTORICAL_CODE, PENDING_CODE)]
EXPECTED_CODES = [b.code for b in BUCKETS if b.expected]


# --------------------------------------------------------------------------- #
# Legacy cross-cutting "theme" folders -> the company bucket they fold into.
# Keys are matched case-insensitively against a top-level folder name.
# (Plan Section 1: "one big Cap Tables folder, one big Investment Memos folder".)
# --------------------------------------------------------------------------- #

# Keys are the *normalized* folder name (leading underscores/spaces stripped,
# lowercased). VoLo's live drive uses a leading-underscore convention for these
# cross-cutting folders (e.g. "_Cap Tables", "_SPAs", "_Investment Memos").
THEME_FOLDER_MAP: dict[str, str] = {
    # generic
    "cap tables": "03", "cap table": "03", "capitalization": "03",
    "investment memos": "01", "investment memo": "01", "memos": "01", "ic memos": "01",
    "financials": "04", "financial models": "04", "models": "04",
    "term sheets": "02", "financing": "02", "legal": "02", "legal docs": "02",
    "board decks": "06", "board materials": "06",
    "investor updates": "07", "updates": "07", "kpis": "07", "monitoring": "07",
    "meeting notes": "08", "call notes": "08", "granola": "08", "transcripts": "08",
    "diligence": "10", "due diligence": "10", "technical diligence": "10",
    "dd": "10", "data received": "10", "data room": "10", "data request": "10",
    "decks": "12", "company decks": "12", "pitch decks": "12", "company materials": "12",
    "grants": "05", "non-dilutive": "05", "non-dilutive updates": "05",
    "impact": "11", "impact reporting": "11",
    "customers": "09", "commercial": "09", "contracts": "09",
    "research": "13", "valuations": "13",
    # VoLo live-drive theme folders (underscore stripped):
    "spas": "02", "d&o insurance": "02", "confirmation of investment 22'": "02",
    "confirmation of investment 22": "02", "org chart": "02", "org charts": "02",
    "org charts & corp structure": "02", "admin & legal docs": "02",
    "comp & options": "03", "compensation & options": "03", "exec comp": "03",
    "budget & pro forma": "04", "2024 audit updates": "04", "audit updates": "04",
    "portco - summary of quarterly financials": "07",
    "portco summary of quarterly financials": "07", "fundraising updates": "07",
    "portco meetings": "08", "board best practices": "06",
    "sss": "15", "ssss": "15",
}

# Generic shared-area names that are never a company.
EXTRA_THEME_FOLDERS = {
    "shared", "misc", "miscellaneous", "general", "templates", "template folder",
    "archive", "old", "to sort", "to file", "inbox", "unsorted",
}

# Top-level folders that are fund/ops level, not a portfolio company and not a
# per-company theme folder -- excluded from per-company structure.
FUND_LEVEL_FOLDERS = {
    "portfolio operations team folder", "monthly all-team portco updates",
    "fund ii transfers to fund ii-a", "2024 projections & updates",
    "old documentation: carta tactyc - portfolio insights configuration",
}


def _norm_folder(name: str) -> str:
    return name.strip().lstrip("_").strip().lower()


def is_theme_folder(name: str) -> bool:
    """A theme folder is any leading-underscore folder, or a known theme name."""
    raw = name.strip()
    if raw.startswith("_"):
        return True
    n = _norm_folder(raw)
    return n in THEME_FOLDER_MAP or n in EXTRA_THEME_FOLDERS


def theme_bucket(name: str) -> str | None:
    """Bucket code a legacy theme folder folds into, else None."""
    return THEME_FOLDER_MAP.get(_norm_folder(name))


# --------------------------------------------------------------------------- #
# Classification rules. For each classifiable bucket we list weighted signals.
#   name_*    -> matched against the (lowercased) filename + its legacy path
#   content_* -> matched against extracted document text (capped)
#   ext       -> filename extensions that nudge toward the bucket
# Multi-word / specific phrases score higher than single generic tokens so that,
# e.g., "board deck" lands in 06 (Board) rather than 12 (generic "deck").
# --------------------------------------------------------------------------- #


@dataclass
class Rule:
    code: str
    name_strong: list[str] = field(default_factory=list)   # specific phrases, weight 3
    name_weak: list[str] = field(default_factory=list)     # generic tokens, weight 1
    content: list[str] = field(default_factory=list)       # content phrases, weight 2 (capped)
    ext: list[str] = field(default_factory=list)           # extension hints, weight 1


RULES: list[Rule] = [
    Rule("01",
         name_strong=["investment memo", "deal memo", "ic memo", "investment committee",
                      "screening memo", "screening note", "ic deck", "ic materials"],
         name_weak=["memo", "screening"],
         content=["investment memo", "investment committee", "recommendation to invest",
                  "investment thesis", "screening"]),
    Rule("02",
         name_strong=["term sheet", "convertible note", "purchase agreement",
                      "stock purchase", "board consent", "side letter", "org chart",
                      "d&o insurance", "operating agreement", "articles of incorporation",
                      "certificate of incorporation", "bylaws", "subscription agreement",
                      "voting agreement", "rofr"],
         name_weak=["safe", "note", "nda", "agreement", "legal", "incorporation",
                    "insurance", "consent", "spa"],
         content=["this term sheet", "simple agreement for future equity",
                  "convertible promissory note", "stock purchase agreement",
                  "the parties agree", "board of directors consent"]),
    Rule("03",
         name_strong=["cap table", "capitalization table", "securities ledger",
                      "stock ledger", "option plan", "option pool", "equity incentive",
                      "fully diluted"],
         name_weak=["captable", "esop", "equity", "shares", "ownership"],
         content=["fully diluted", "shares outstanding", "ownership %", "option pool",
                  "preferred stock", "common stock", "capitalization"],
         ext=[".xlsx", ".xls", ".gsheet"]),
    Rule("04",
         name_strong=["financial model", "operating model", "income statement",
                      "balance sheet", "cash flow", "p&l", "profit and loss",
                      "financial statements", "three statement", "3-statement"],
         name_weak=["model", "financials", "budget", "projection", "projections",
                    "forecast", "actuals"],
         content=["income statement", "balance sheet", "statement of cash flows",
                  "gross margin", "ebitda", "revenue", "operating expenses"],
         ext=[".xlsx", ".xls", ".xlsm", ".gsheet"]),
    Rule("05",
         name_strong=["grant award", "grant agreement", "non-dilutive", "arpa-e",
                      "sbir", "sttr", "doe grant", "award letter"],
         name_weak=["grant", "award", "subsidy"],
         content=["grant award", "award number", "period of performance", "non-dilutive"]),
    Rule("06",
         name_strong=["board deck", "board meeting", "board minutes", "board update",
                      "board resolution", "board package", "board materials"],
         name_weak=["board", "minutes", "resolution", "governance"],
         content=["board of directors meeting", "minutes of the meeting", "resolved that"]),
    Rule("07",
         name_strong=["investor update", "monthly update", "quarterly update",
                      "kpi dashboard", "monitoring report", "metrics update"],
         name_weak=["update", "kpi", "kpis", "monitoring", "metrics", "dashboard"],
         content=["investor update", "this month", "mrr", "arr", "runway",
                  "key metrics", "highlights", "lowlights"]),
    Rule("08",
         name_strong=["meeting notes", "call notes", "granola transcript",
                      "call transcript", "intro call", "catch up", "1:1 notes"],
         name_weak=["notes", "call", "meeting", "transcript", "sync"],
         content=["call notes", "meeting notes", "attendees", "action items", "transcript"]),
    Rule("09",
         name_strong=["customer contract", "letter of intent", "commercial pipeline",
                      "master service agreement", "purchase order", "customer reference",
                      "sales pipeline"],
         name_weak=["customer", "contract", "loi", "pipeline", "commercial", "msa",
                    "sales", "offtake"],
         content=["letter of intent", "master service agreement", "customer",
                  "purchase order", "this agreement is between"]),
    Rule("10",
         name_strong=["technical diligence", "due diligence", "diligence report",
                      "data received", "tech review", "ip assignment", "patent",
                      "technical review", "dd report"],
         name_weak=["diligence", "technical", "ip", "patents", "dd"],
         content=["technical due diligence", "patent", "intellectual property",
                  "technology readiness", "data room"]),
    Rule("11",
         name_strong=["impact report", "carbon abatement", "life cycle assessment",
                      "emissions report", "climate impact", "energy metrics",
                      "tco2", "co2 abatement"],
         name_weak=["impact", "carbon", "emissions", "climate", "lca", "abatement"],
         content=["tons of co2", "carbon abatement", "life cycle assessment",
                  "scope 1", "scope 2", "ghg"]),
    Rule("12",
         name_strong=["company deck", "pitch deck", "one pager", "one-pager",
                      "product overview", "company overview", "investor deck"],
         name_weak=["deck", "pitch", "onepager", "overview", "product", "presentation"],
         content=["pitch", "our product", "the problem", "the solution", "go to market"],
         ext=[".pptx", ".ppt", ".key", ".gslides"]),
    Rule("13",
         name_strong=["409a", "409a valuation", "valuation report", "market report",
                      "industry report", "analyst report", "third party", "third-party"],
         name_weak=["valuation", "research", "market", "analyst"],
         content=["409a", "fair market value", "market size", "tam", "industry analysis"]),
]

RULE_BY_CODE: dict[str, Rule] = {r.code: r for r in RULES}

# Weights for the different signal kinds.
W_THEME_ORIGIN = 5      # the legacy theme folder a file came from
W_NAME_STRONG = 3
W_NAME_WEAK = 1
W_CONTENT = 2           # per distinct content phrase, capped
W_CONTENT_CAP = 4       # max total content contribution
W_EXT = 1
W_PATH = 5              # the nearest recognized type/container folder above the file
W_PATH_ANCESTOR = 1.5  # a further-up recognized folder (weaker corroboration)

# Intra-company sub-folder names -> bucket. On the live drive the company's own
# sub-folders are extremely informative (e.g. "00. Data Request", "Board Meetings",
# "Financial Model", "Closing Binder"). Each substring is matched against every
# normalized path component, so a file deep under .../Financials/2024/Q1/... is
# pulled toward 04 even when its own name is generic. Order matters: the most
# specific phrases are listed first and win.
PATH_SIGNAL_MAP: list[tuple[str, str]] = [
    ("data request", "10"), ("data room", "10"), ("diligence", "10"),
    ("technical", "10"), ("seed diligence", "10"),
    ("closing binder", "02"), ("term sheet", "02"), ("admin & legal", "02"),
    ("admin and legal", "02"), ("subscription agreement", "02"),
    ("incorporation", "02"), ("corp structure", "02"), ("org chart", "02"),
    ("d&o", "02"), ("confirmation of investment", "02"), ("legal", "02"),
    ("insurance", "02"),
    # NB: round/event containers ("Series A", "Bridge", "Data Room 2022") are NOT
    # type signals -- they hold cap tables, financials, memos, legal all together,
    # so the leaf folder + filename decide, not the round.
    ("cap table", "03"), ("captable", "03"), ("compensation", "03"),
    ("exec comp", "03"), ("comp & options", "03"), ("options", "03"),
    ("securities", "03"), ("409a", "13"),
    ("financial model", "04"), ("financial statement", "04"), ("budget", "04"),
    ("pro forma", "04"), ("financials", "04"), ("projections", "04"),
    ("audit", "04"), ("quarterly financials", "04"),
    ("non-dilutive", "05"), ("non dilutive", "05"), ("grant", "05"),
    ("board meeting", "06"), ("board material", "06"), ("board deck", "06"),
    ("board notes", "06"), ("minutes", "06"), ("board seat", "06"),
    ("finance committee", "06"), ("board", "06"),
    ("monitoring", "07"), ("investor update", "07"), ("fundraising", "07"),
    ("kpi", "07"), ("quarterly update", "07"),
    ("meeting", "08"), ("call notes", "08"), ("interactions", "08"),
    ("contract", "09"), ("customer", "09"), ("commercial", "09"), ("sales", "09"),
    ("impact", "11"), ("carbon", "11"),
    ("presentations", "12"), ("company materials", "12"), ("brand", "12"),
    ("logo", "12"), ("collateral", "12"), ("one-pager", "12"),
    ("core deck", "12"), ("pitch deck", "12"), ("deck", "12"),
    ("investment memo", "01"), ("ic memo", "01"),
]

# Decision thresholds (Section 2d/2e: conservative; only genuine uncertainty -> human).
MIN_SCORE = 3.0         # below this absolute score -> 15 Unclassified / Pending Review
MIN_CONFIDENCE = 0.50   # below this top-vs-rest margin -> 15 (unless a theme origin decided it)
STRONG_SCORE = 6.0      # at/above this, place even if the margin is thin


# --------------------------------------------------------------------------- #
# Confidentiality + version vocab (Section 5: never serve internal as external;
# drafts/finals are versions, but internal/external/redacted are *different docs*).
# --------------------------------------------------------------------------- #

CONFIDENTIALITY_TOKENS = ["internal", "external", "redacted", "public", "confidential"]
STAGE_RANK = {"final": 3, "executed": 4, "signed": 4, "clean": 2, "draft": 1, "wip": 0}

# Filename date patterns, richest first.
DATE_PATTERNS: list[tuple[str, str]] = [
    (r"(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])", "ymd"),   # 2024-03-15
    (r"(0[1-9]|1[0-2])[-_.](0[1-9]|[12]\d|3[01])[-_.](20\d{2})", "mdy"),     # 03-15-2024
    (r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-_. ]?(20\d{2})\b", "mon_y"),
    (r"\bq([1-4])[-_. ]?(20\d{2})\b", "q_y"),
    (r"\b(20\d{2})\b", "y"),
]

# Tokens stripped when normalizing a filename into a version-family key.
VERSION_TOKENS = [
    r"\bv\d+(\.\d+)?\b", r"\bversion\s*\d+\b", r"\bdraft\b", r"\bfinal\b",
    r"\bexecuted\b", r"\bsigned\b", r"\bclean\b", r"\bwip\b", r"\bcopy\b",
    r"\bredline\b", r"\(\d+\)", r"\brev\s*\d+\b",
]

# Files we never catalog as content (OS/Office cruft).
IGNORE_FILENAMES = {".ds_store", "thumbs.db", "desktop.ini", ".gitkeep", "icon\r"}
IGNORE_PREFIXES = ("~$", "._")

# Google-native pointer files: tiny JSON shortcuts to an online Google Doc.
# Content cannot be read locally (would need a Drive API export), so we classify
# them by name + path and flag them. The native kind biases the bucket.
GNATIVE_EXTS = {".gdoc", ".gsheet", ".gslides", ".gdraw", ".gform", ".gtable",
                ".gmap", ".gsite", ".gjam", ".glink", ".gnote", ".gshortcut"}
ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".7z", ".rar"}
EMAIL_EXTS = {".msg", ".eml"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}
LEGACY_BINARY_EXTS = {".doc", ".ppt"}     # old Office formats we can't easily read

# Skip *content* extraction above this size (still catalogued by name/path/metadata).
MAX_EXTRACT_MB = 30


def clean_ext(filename: str) -> str:
    """Return a sane lowercased extension, or '' when the dot is part of the name.

    Guards against filenames like '1.25 Cambium Board Meeting' or
    'Master Summary 10.25' where naive suffix parsing yields a bogus extension.
    """
    import os
    ext = os.path.splitext(filename)[1].lower()
    body = ext[1:]
    if not body or " " in ext or len(body) > 5 or not body.isalnum():
        return ""
    return ext
