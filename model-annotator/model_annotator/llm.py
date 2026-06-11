"""Thin Anthropic API adapter — the ONLY module that imports `anthropic`.

The LLM is an optional assistant, never an oracle: it classifies ambiguous row
labels into the canonical ontology and polishes finding narratives. It never
performs arithmetic, never supplies a number, never estimates a value, and the
numeric-consistency verifier (narrate.py) re-checks every numeral it touches.

With ``--no-llm`` (or no API key, or no `anthropic` package) everything in
this module degrades to no-ops and the tool stays fully functional.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-4-8"

_CLASSIFY_SYSTEM = (
    "You classify financial-model row labels into a fixed ontology for a model-diligence tool. "
    "You receive one label (and the section header above it) from a startup's Excel model. "
    "Reply with EXACTLY one ontology id from the provided list, or the word none. "
    "No punctuation, no explanation. Never invent ids. If the label is a derived/analyst "
    "metric (growth, ratio, cumulative, per-unit), reply none."
)

_POLISH_SYSTEM = (
    "You are editing diligence notes written by a financial-model analysis tool for venture "
    "investors. Improve clarity and flow ONLY. Hard rules: keep every number, cell reference "
    "(like Sheet!C42), and percentage EXACTLY as written — do not round, reformat, convert, or "
    "introduce any numeral that is not already present. Keep the neutral, question-forward tone. "
    "Reply with the edited text only."
)


class LLMClient:
    """Lazy, fail-quiet Anthropic client. ``available`` gates every call."""

    def __init__(self, enabled: bool = True, model: Optional[str] = None):
        self.model = model or os.environ.get("MODEL_ANNOTATOR_LLM_MODEL", DEFAULT_MODEL)
        self.disabled_reason: Optional[str] = None
        self._client = None
        if not enabled:
            self.disabled_reason = "--no-llm"
            return
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            self.disabled_reason = "no ANTHROPIC_API_KEY in environment"
            return
        try:
            import anthropic
            self._client = anthropic.Anthropic()
        except ImportError:
            self.disabled_reason = "anthropic package not installed (pip install model-annotator[llm])"
        except Exception as exc:  # construction should never kill the pipeline
            self.disabled_reason = f"client construction failed: {exc}"

    @property
    def available(self) -> bool:
        return self._client is not None

    def _call(self, system: str, user: str, max_tokens: int) -> Optional[str]:
        if self._client is None:
            return None
        try:
            import anthropic
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": user}],
            )
            return next((b.text for b in resp.content if b.type == "text"), None)
        except anthropic.APIError as exc:
            log.warning("LLM call failed (%s); continuing deterministically", type(exc).__name__)
            return None
        except Exception:
            log.warning("LLM call failed unexpectedly; continuing deterministically", exc_info=True)
            return None

    # -- (a) ambiguous-label classification (labels only, never values) -----
    def classify_label(self, label: str, section: str,
                       candidate_ids: list[str]) -> Optional[tuple[str, float]]:
        from .schema import ALL_CANONICAL_IDS
        ids = sorted(ALL_CANONICAL_IDS)
        user = (f"Ontology ids: {', '.join(ids)}\n"
                f"Section header above the row: {section or '(none)'}\n"
                f"Row label: {label!r}\n"
                + (f"Heuristic shortlist: {', '.join(candidate_ids)}\n" if candidate_ids else "")
                + "Best ontology id (or none):")
        out = self._call(_CLASSIFY_SYSTEM, user, max_tokens=16)
        if not out:
            return None
        token = out.strip().split()[0].strip(".,`'\"").lower()
        if token in ALL_CANONICAL_IDS:
            return token, 0.7
        return None

    # -- (b) narrative polish (numbers re-verified by the caller) -----------
    def polish_narrative(self, text: str) -> Optional[str]:
        if len(text) < 40:
            return None
        return self._call(_POLISH_SYSTEM, text, max_tokens=1024)


_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def extract_numerals(text: str) -> list[str]:
    """All numerals in a text, normalized (commas stripped). Cell refs like C42
    are deliberately included — a changed reference is as bad as a changed value."""
    return [m.group(0).replace(",", "") for m in _NUM.finditer(text)]


def numerals_consistent(polished: str, original: str) -> bool:
    """Every numeral in the polished text must already exist in the original."""
    allowed = set(extract_numerals(original))
    return all(n in allowed for n in extract_numerals(polished))


def polish_with_verification(llm: LLMClient, text: str) -> tuple[str, bool]:
    """Polished text iff the numeric-consistency check passes; original otherwise.

    Returns ``(text, was_polished)``.
    """
    if not llm.available:
        return text, False
    polished = llm.polish_narrative(text)
    if polished and numerals_consistent(polished, text):
        return polished.strip(), True
    if polished:
        log.warning("LLM polish altered a numeral; falling back to template text")
    return text, False
