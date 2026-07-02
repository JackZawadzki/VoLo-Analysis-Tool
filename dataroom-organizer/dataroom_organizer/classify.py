"""
Deterministic, simplest-baseline-first document classification.

The plan (Section 2d, "simplest method first"; Risks, "AI sounding confident but
being wrong") asks for a cheap, predictable, auditable baseline that always runs
first. This module is that baseline: a transparent weighted-signal scorer over
the rules in config.py. Every decision records the exact signals that fired and a
confidence score, and anything genuinely uncertain is routed to 15 / Pending for
a human.

An optional LLM escalation hook can be supplied for the low-confidence remainder
only -- it is OFF by default, and even when on it must return one of the 16 codes
plus a rationale; it never invents figures (numbers are read by code in extract).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import config as C


# --------------------------------------------------------------------------- #
# Date / confidentiality / version parsing
# --------------------------------------------------------------------------- #

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_date_from_name(name: str) -> Optional[str]:
    """Best-effort ISO date (YYYY-MM-DD) parsed from a filename, else None."""
    low = name.lower()
    for pat, kind in C.DATE_PATTERNS:
        m = re.search(pat, low)
        if not m:
            continue
        try:
            if kind == "ymd":
                y, mo, d = m.group(1), m.group(2), m.group(3)
                return f"{y}-{mo}-{d}"
            if kind == "mdy":
                mo, d, y = m.group(1), m.group(2), m.group(3)
                return f"{y}-{mo}-{d}"
            if kind == "mon_y":
                mo = _MONTHS[m.group(1)[:3]]
                return f"{m.group(2)}-{mo:02d}-01"
            if kind == "q_y":
                q = int(m.group(1))
                mo = {1: 3, 2: 6, 3: 9, 4: 12}[q]
                return f"{m.group(2)}-{mo:02d}-01"
            if kind == "y":
                return f"{m.group(1)}-01-01"
        except Exception:
            continue
    return None


def parse_confidentiality(text: str) -> str:
    low = text.lower()
    for tok in C.CONFIDENTIALITY_TOKENS:
        if re.search(rf"\b{re.escape(tok)}\b", low):
            return tok
    return ""


def parse_stage(text: str) -> str:
    low = text.lower()
    best, best_rank = "", -1
    for stage, rank in C.STAGE_RANK.items():
        if re.search(rf"\b{re.escape(stage)}\b", low) and rank > best_rank:
            best, best_rank = stage, rank
    return best


def version_family_key(company: str, bucket: str, confidentiality: str, name: str,
                       doc_date: str = "", keep_date: bool = False, scope: str = "") -> str:
    """Normalize a filename into a key that groups versions of the same document.

    Confidentiality is part of the key: an internal memo and its external twin are
    *different documents*, not versions of each other (plan, Risks/Sensitive content).

    `scope` (the file's folder) is part of the key so two same-named files in
    DIFFERENT folders (e.g. a 'financials.xlsx' in each quarter's folder) are NOT
    mistaken for versions of one another -- on a deep real drive that false merge
    would wrongly bury a current document in Historical. Versions are detected
    within a folder, which is where they actually accumulate.

    For periodic *series* buckets (investor updates, board decks, meeting notes;
    keep_date=True) the date is part of the identity, not a version marker -- each
    monthly update is its own document to preserve, never superseded by the next
    (Joseph's note: "multiple time-stamped files ... preserve this").
    """
    base = name.lower()
    base = re.sub(r"\.[a-z0-9]+$", "", base)                 # drop extension
    if not keep_date:
        for pat, _ in C.DATE_PATTERNS:
            base = re.sub(pat, " ", base)
    for tok in C.VERSION_TOKENS:
        base = re.sub(tok, " ", base)
    base = re.sub(r"[^a-z0-9]+", " ", base).strip()
    base = re.sub(r"\s+", " ", base)
    date_part = doc_date if keep_date else ""
    return f"{company}|{bucket}|{confidentiality}|{scope}|{base}|{date_part}"


# --------------------------------------------------------------------------- #
# Baseline classifier
# --------------------------------------------------------------------------- #

LLMHook = Callable[[dict], Optional[dict]]   # (context) -> {"code","confidence","rationale"} | None


def _path_signal_scores(source_rel: str) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Score buckets from the folder names on the path, weighting folders closest
    to the file most. Only the three nearest ancestors contribute; the leaf folder
    gets full weight so a typed sub-folder overrides a round/event container above."""
    scores: dict[str, float] = {}
    ev: dict[str, list[str]] = {}
    parts = source_rel.replace("\\", "/").split("/")[:-1]   # exclude the filename
    nearest_found = False
    for raw in reversed(parts):                            # leaf parent -> upward
        comp = re.sub(r"[_\-./\\]+", " ", raw.lower()).strip()
        comp = re.sub(r"\s+", " ", comp)
        if not comp:
            continue
        for phrase, code in C.PATH_SIGNAL_MAP:
            if phrase in comp:
                # the nearest recognized folder is decisive; folders further up
                # only corroborate (and can't overturn the nearest one's weight).
                w = C.W_PATH if not nearest_found else C.W_PATH_ANCESTOR
                scores[code] = scores.get(code, 0.0) + w
                ev.setdefault(code, []).append(f"path:'{raw}'~'{phrase}'(+{w:.1f})")
                nearest_found = True
                break   # most specific (list is ordered) wins for this component
    return scores, ev


def _score_bucket(rule: C.Rule, hay_name: str, hay_content: str, ext: str,
                  theme_code: Optional[str]) -> tuple[float, list[str]]:
    score = 0.0
    ev: list[str] = []

    if theme_code == rule.code:
        score += C.W_THEME_ORIGIN
        ev.append(f"theme-origin->+{C.W_THEME_ORIGIN}")

    for phrase in rule.name_strong:
        if phrase in hay_name:
            score += C.W_NAME_STRONG
            ev.append(f"name:'{phrase}'->+{C.W_NAME_STRONG}")
    for tok in rule.name_weak:
        if re.search(rf"\b{re.escape(tok)}\b", hay_name):
            score += C.W_NAME_WEAK
            ev.append(f"name~'{tok}'->+{C.W_NAME_WEAK}")

    content_hits = 0.0
    for phrase in rule.content:
        if phrase in hay_content:
            content_hits += C.W_CONTENT
            ev.append(f"content:'{phrase}'->+{C.W_CONTENT}")
            if content_hits >= C.W_CONTENT_CAP:
                break
    score += min(content_hits, C.W_CONTENT_CAP)

    if ext in rule.ext:
        score += C.W_EXT
        ev.append(f"ext:{ext}->+{C.W_EXT}")

    return score, ev


def classify(*, filename: str, source_rel: str, text: str, ext: str,
             theme_code: Optional[str], llm_hook: Optional[LLMHook] = None) -> dict:
    """Return classification dict for one document.

    Keys: code, bucket_name, confidence, needs_review, evidence(list), method.
    """
    # Normalize separators so 'term_sheet'/'term-sheet' match the 'term sheet'
    # phrase signals (otherwise a body full of cap-table words can win).
    hay_name = re.sub(r"[_\-./\\]+", " ", f"{filename} {source_rel}".lower())
    hay_content = (text or "").lower()
    ext = ext.lower()

    scores: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}
    for rule in C.RULES:
        s, ev = _score_bucket(rule, hay_name, hay_content, ext, theme_code)
        scores[rule.code] = s
        evidence[rule.code] = ev

    # Intra-folder path signals: the company's own sub-folder names are strong
    # evidence on the live drive (e.g. .../Financials/2024/Q1/...). The folder
    # closest to the file weighs most, so a typed leaf folder overrides a round
    # container above it.
    p_scores, p_ev = _path_signal_scores(source_rel)
    for code, sc in p_scores.items():
        scores[code] = scores.get(code, 0.0) + sc
        evidence.setdefault(code, []).extend(p_ev[code])

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_code, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = top_score / (top_score + second_score) if top_score > 0 else 0.0

    decided_by_theme = (theme_code == top_code and theme_code is not None)

    # Decision (conservative): strong score, or decent score + clear margin, places.
    if top_score >= C.STRONG_SCORE or decided_by_theme:
        code, conf, needs_review = top_code, _conf(top_score, margin), False
        method = "baseline"
    elif top_score >= C.MIN_SCORE and margin >= C.MIN_CONFIDENCE:
        code, conf, needs_review = top_code, _conf(top_score, margin), False
        method = "baseline"
    else:
        code, conf, needs_review = C.PENDING_CODE, _conf(top_score, margin), True
        method = "baseline-uncertain"

    ev_out = list(evidence.get(top_code, [])) or ["no-signal"]

    # Optional LLM escalation, ONLY for the uncertain remainder.
    if needs_review and llm_hook is not None:
        ctx = {
            "filename": filename, "source_rel": source_rel,
            "text_excerpt": (text or "")[:4000],
            "candidates": [{"code": c, "score": s} for c, s in ranked[:4]],
            "buckets": {b.code: b.name for b in C.BUCKETS},
        }
        try:
            res = llm_hook(ctx)
        except Exception:
            res = None
        if res and res.get("code") in C.BUCKET_BY_CODE:
            code = res["code"]
            conf = float(res.get("confidence", 0.6))
            needs_review = conf < 0.6
            method = "llm-escalation"
            ev_out = [f"llm:{res.get('rationale','(no rationale)')[:200]}"]

    return {
        "code": code,
        "bucket_name": C.BUCKET_BY_CODE[code].name,
        "confidence": round(conf, 3),
        "needs_review": needs_review,
        "evidence": ev_out,
        "method": method,
        "_scores": scores,
    }


def _conf(top_score: float, margin: float) -> float:
    if top_score <= 0:
        return 0.15
    # blend absolute strength (saturating) with the top-vs-rest margin
    strength = min(1.0, top_score / 8.0)
    return max(0.15, min(0.99, 0.5 * strength + 0.5 * margin))


def mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()
    except Exception:
        return ""
