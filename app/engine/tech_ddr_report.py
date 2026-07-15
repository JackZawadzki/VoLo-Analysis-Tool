"""
tech_ddr_report.py
==================
PDF generation for the **Technical Due Diligence Report** (deep-tech track).

Self-contained (does not import from ddr_report.py). A repeatable, investor-facing
venture-diligence brief structured as:

  1. Executive Summary
  2. Technology Explanation
  3. Claimed Product / Use Case
  4. Theory-to-Product Bridge
  5. Prior Work & Literature Map  (+ Further Reading)
  6. Benchmarking Framework
  7. Commercial Implications
  8. Manufacturing Scale-Up & Commercialization Risk
  9. Key Diligence Questions / Data Requests
     + Citations

It is informational only — it frames the technology and the diligence questions
and contains NO investment recommendation. The free-text input is treated as the
FOUNDER's claim (the researcher who brought the paper + a product idea).

Glyph handling: a bundled DejaVuSans family (app/engine/fonts) is registered so
Greek letters, math operators and super/subscripts render natively; anything the
font cannot draw is transliterated/stripped so the PDF never shows tofu boxes.
"""

import io
import os
import re
import unicodedata
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Flowable,
)
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

# ── Colors ───────────────────────────────────────────────────────────────────

VOLO_GREEN = "#2d5f3f"
ACCENT_BLUE = "#3a6ea8"
GOOD_GREEN = "#1f7a4d"
WARN_ORANGE = "#c8721f"
BAD_RED = "#b3402f"
MUTED = "#666666"


# ── Fonts (bundled Unicode TTF so math/Greek render; no tofu) ────────────────

_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONT = "Helvetica"
FONT_B = "Helvetica-Bold"
FONT_I = "Helvetica-Oblique"
FONT_BI = "Helvetica-BoldOblique"
_SUPPORTED = None  # set of codepoints the active font can render (None = Helvetica)


def _register_fonts():
    global FONT, FONT_B, FONT_I, FONT_BI, _SUPPORTED
    try:
        faces = [
            ("DejaVuSans", "DejaVuSans.ttf"),
            ("DejaVuSans-Bold", "DejaVuSans-Bold.ttf"),
            ("DejaVuSans-Oblique", "DejaVuSans-Oblique.ttf"),
            ("DejaVuSans-BoldOblique", "DejaVuSans-BoldOblique.ttf"),
        ]
        for name, fn in faces:
            pdfmetrics.registerFont(TTFont(name, os.path.join(_FONT_DIR, fn)))
        registerFontFamily(
            "DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold",
            italic="DejaVuSans-Oblique", boldItalic="DejaVuSans-BoldOblique",
        )
        FONT, FONT_B = "DejaVuSans", "DejaVuSans-Bold"
        FONT_I, FONT_BI = "DejaVuSans-Oblique", "DejaVuSans-BoldOblique"
        face = pdfmetrics.getFont("DejaVuSans").face
        cmap = getattr(face, "charToGlyph", None)
        if cmap:
            _SUPPORTED = set(cmap.keys())
    except Exception as ex:  # pragma: no cover - fall back to built-in Helvetica
        print(f"[TechDDR] DejaVu font registration failed, using Helvetica: {ex}", flush=True)
        _SUPPORTED = None


_register_fonts()

# Transliterations used ONLY when the active font lacks the glyph (so DejaVu still
# renders Greek/operators natively when present).
_TRANSLIT = {
    "■": "", "□": "", "▪": "", "◼": "", "●": "-", "•": "-", "▸": "-", "‣": "-",
    "→": "->", "⇒": "=>", "←": "<-", "↔": "<->", "↦": "->",
    "≈": "~", "≅": "~", "≃": "~", "≤": "<=", "≥": ">=", "≠": "!=",
    "∝": "proportional to", "∞": "infinity", "≪": "<<", "≫": ">>",
    "×": "x", "÷": "/", "−": "-", "–": "-", "—": "-", "‐": "-", "⁻": "-",
    "’": "'", "‘": "'", "“": '"', "”": '"', "…": "...", "′": "'", "″": '"',
    "√": "sqrt", "∑": "sum", "∏": "prod", "∫": "integral", "∮": "integral",
    "∇": "grad", "∂": "d", "∆": "Delta", "Δ": "Delta", "∈": "in", "∉": "not in",
    "≡": "=", "≜": "=", "⊗": "(x)", "⊕": "(+)", "·": "*", "∙": "*",
}


# Always removed even if the font CAN draw them — these are tofu/box/replacement
# glyphs or stray decorative squares that should never appear in prose.
_FORCE_STRIP = {"�", "■", "□", "▪", "▫", "◼", "◻", "◾", "◽", "■", "□"}


def _font_safe(s: str) -> str:
    """Replace any character the active font cannot render with a transliteration
    or NFKD-ASCII fallback, so the PDF never shows a tofu box. A small set of
    box/replacement glyphs is dropped unconditionally."""
    if not s:
        return s
    out = []
    for ch in s:
        if ch in _FORCE_STRIP:
            continue
        o = ord(ch)
        ok = (o in _SUPPORTED) if _SUPPORTED is not None else (o < 0x100)
        if ok or ch in "\n\r\t":
            out.append(ch)
            continue
        rep = _TRANSLIT.get(ch)
        if rep is None:
            dec = unicodedata.normalize("NFKD", ch)
            if _SUPPORTED is not None:
                rep = "".join(c for c in dec if ord(c) in _SUPPORTED)
            else:
                rep = "".join(c for c in dec if ord(c) < 0x100)
        out.append(rep or "")
    return "".join(out)


# ── Escaping (literal markup handling; keeps a safe subset of tags) ──────────

def _esc(text) -> str:
    if not isinstance(text, str):
        text = str(text)
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("$", "&#36;"))


_SAFE_TAG_RE = re.compile(
    r'</?(?:b|i|u|br|br/|super|sub|font|a|para|seq|seqreset|onDraw|index|img)(?:\s[^>]*)?>',
    re.IGNORECASE,
)
_ENTITY_RE = re.compile(r'&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);')


def _esc_preserving_entities(text: str) -> str:
    parts = _ENTITY_RE.split(text)
    entities = _ENTITY_RE.findall(text)
    escaped = [_esc(p) for p in parts]
    out = []
    for i, part in enumerate(escaped):
        out.append(part)
        if i < len(entities):
            out.append(entities[i])
    return "".join(out)


def _p(text, style) -> Paragraph:
    if not isinstance(text, str):
        text = str(text)
    text = _font_safe(text)  # strip/transliterate unrenderable glyphs first
    parts = _SAFE_TAG_RE.split(text)
    tags = _SAFE_TAG_RE.findall(text)
    escaped_parts = [_esc_preserving_entities(p) for p in parts]
    out = []
    for i, part in enumerate(escaped_parts):
        out.append(part)
        if i < len(tags):
            out.append(tags[i])
    return Paragraph("".join(out), style)


def _safe_href(url: str) -> str:
    u = str(url or "").strip()
    if not u:
        return ""
    if u.lower().startswith("doi:"):
        u = u[4:].strip()
    if u.startswith("10.") and "/" in u:
        u = "https://doi.org/" + u
    if not (u.startswith("http://") or u.startswith("https://")):
        return ""
    return u.replace("&", "&amp;").replace('"', "%22").replace("<", "").replace(">", "")


def _link(text: str, url: str) -> str:
    href = _safe_href(url)
    if href:
        return f'<a href="{href}" color="{ACCENT_BLUE}"><u>{text}</u></a>'
    return str(text)


def _cite(src) -> str:
    """A citation as inline markup. Accepts a bare string (URL or name) OR a
    dict {title,url,publisher,link}. Renders a clickable link with the title/name
    as visible text when a real URL exists, else just the title/name (the
    safeguard — still findable). Never raises on odd input (numbers, None, etc.)."""
    if isinstance(src, dict):
        url = str(src.get("url") or src.get("link") or "").strip()
        title = str(src.get("title") or src.get("publisher") or src.get("name") or url or "Source").strip()
        return _link(title, url)
    return _link(str(src).strip(), str(src).strip())


def _count_unique_sources(analysis: dict) -> int:
    """Deduped external-source count reported IDENTICALLY in the methodology line
    and the Citations appendix (fix 31) — mirrors the appendix's own collection
    (section source/sources/link fields, skipping the related_research reading
    list, plus _research_sources). Never the model's self-reported count."""
    acc = []

    def _walk(obj):
        if isinstance(obj, dict):
            for k in ("sources", "source", "link"):
                v = obj.get(k)
                if isinstance(v, list):
                    for s in v:
                        if isinstance(s, dict) or (isinstance(s, str) and s.strip()):
                            acc.append(s)
                elif isinstance(v, dict) or (isinstance(v, str) and v.strip()):
                    acc.append(v)
            for vk, vv in obj.items():
                if vk == "related_research":
                    continue
                if isinstance(vv, (dict, list)):
                    _walk(vv)
        elif isinstance(obj, list):
            for it in obj:
                _walk(it)

    for key in ("prior_work_literature_map", "benchmarking_framework",
                "theory_to_product_bridge", "commercial_implications",
                "manufacturing_scaleup_risk", "technology_explanation",
                "claimed_product", "executive_summary"):
        _walk(analysis.get(key))
    acc.extend(analysis.get("_research_sources", []) or [])
    seen = set()
    for s in acc:
        if isinstance(s, dict):
            k = str(s.get("url") or s.get("link") or s.get("title") or "").strip().lower()
        else:
            k = str(s).strip().lower()
        if k:
            seen.add(k)
    return len(seen)


def _risk_color(sev: str) -> str:
    s = (sev or "").upper()
    return {"HIGH": BAD_RED, "MEDIUM": WARN_ORANGE, "LOW": GOOD_GREEN}.get(s, MUTED)


def _conf_color(level: str) -> str:
    s = (level or "").upper()
    return {"HIGH": GOOD_GREEN, "MEDIUM": ACCENT_BLUE, "LOW": WARN_ORANGE}.get(s, MUTED)


def _clarity_color(c: str) -> str:
    s = (c or "").upper()
    return {"CLEAR": GOOD_GREEN, "PARTIALLY CLEAR": WARN_ORANGE, "UNCLEAR": BAD_RED}.get(s, MUTED)


# ── Styles ───────────────────────────────────────────────────────────────────

def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle('TTitle', parent=base['Heading1'], fontSize=22,
                                textColor=colors.HexColor(VOLO_GREEN), spaceAfter=14,
                                alignment=TA_CENTER, fontName=FONT_B),
        "heading": ParagraphStyle('THeading', parent=base['Heading2'], fontSize=15,
                                  textColor=colors.HexColor(VOLO_GREEN), spaceAfter=9,
                                  spaceBefore=15, fontName=FONT_B),
        "sub": ParagraphStyle('TSub', parent=base['Heading3'], fontSize=11.5,
                              textColor=colors.HexColor('#1a472a'), spaceAfter=5,
                              spaceBefore=9, fontName=FONT_B),
        "body": ParagraphStyle('TBody', parent=base['BodyText'], fontSize=10,
                               leading=14, spaceAfter=8, alignment=TA_JUSTIFY, fontName=FONT),
        "small": ParagraphStyle('TSmall', parent=base['BodyText'], fontSize=9,
                                leading=12, spaceAfter=6, alignment=TA_JUSTIFY, fontName=FONT),
    }


_TOC_ORDER = [
    ("exec", "Executive Summary"),
    ("tech", "Technology Explanation"),
    ("product", "Claimed Product / Use Case"),
    ("bridge", "Theory-to-Product Bridge"),
    ("prior", "Prior Work & Literature Map"),
    ("bench", "Benchmarking Framework"),
    ("commercial", "Commercial Implications"),
    ("manufacturing", "Manufacturing Scale-Up & Risk"),
    ("diligence", "Key Diligence Questions"),
    ("citations", "Citations"),
]


def generate_tech_report_pdf(analysis: dict, output_path: str):
    """Two-pass build (for accurate TOC page numbers)."""
    S = _styles()
    toc_tracker = {}

    class _Anchor(Flowable):
        def __init__(self, key):
            Flowable.__init__(self)
            self.key = key
            self.width = 0
            self.height = 0

        def draw(self):
            toc_tracker[self.key] = self.canv.getPageNumber()

    class _Commentary(Flowable):
        def __init__(self, name, label, h=1.2 * inch):
            Flowable.__init__(self)
            self.name = name
            self.label = label
            self.fw = 6.5 * inch
            self.fh = h
            self.width = self.fw
            self.height = h + 16

        def draw(self):
            c = self.canv
            c.setStrokeColor(colors.HexColor('#d4e6da'))
            c.setLineWidth(0.5)
            c.line(0, self.height, self.fw, self.height)
            c.setFont(FONT_B, 8)
            c.setFillColor(colors.HexColor(VOLO_GREEN))
            c.drawString(2, self.fh + 3, _font_safe(self.label))
            c.acroForm.textfield(
                name=self.name, tooltip=_font_safe(self.label), x=0, y=0,
                width=self.fw, height=self.fh, borderStyle='inset',
                borderColor=colors.HexColor('#c8dcc8'),
                fillColor=colors.HexColor('#f8fbf9'), textColor=colors.black,
                forceBorder=True, relative=True, fieldFlags='multiline',
                maxlen=100000, fontSize=9,
            )

    def _bullets(items, style):
        out = []
        for it in (items or []):
            if isinstance(it, str) and it.strip():
                out.append(_p(f"- {it}", style))
        return out

    def _toc(entries=None):
        items = [_p("TABLE OF CONTENTS",
                    ParagraphStyle('TTOC', parent=S['title'], fontSize=18)),
                 Spacer(1, 0.12 * inch),
                 Table([['']], colWidths=[6.5 * inch],
                       style=TableStyle([('LINEBELOW', (0, 0), (-1, -1), 1.5,
                                          colors.HexColor(VOLO_GREEN))])),
                 Spacer(1, 0.22 * inch)]
        if entries:
            name_st = ParagraphStyle('TN', parent=S['body'], fontSize=11, leading=18)
            pg_st = ParagraphStyle('TP', parent=S['body'], fontSize=11, leading=18,
                                   alignment=TA_CENTER, fontName=FONT_B,
                                   textColor=colors.HexColor(VOLO_GREEN))
            rows = [[Paragraph(_font_safe(label), name_st), Paragraph(str(entries[key]), pg_st)]
                    for key, label in _TOC_ORDER if key in entries]
            if rows:
                t = Table(rows, colWidths=[5.8 * inch, 0.7 * inch])
                t.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                    ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#e0e8e2')),
                ]))
                items.append(t)
        else:
            items.append(Spacer(1, 5 * inch))
        items.append(PageBreak())
        return items

    def _section_sources(obj, acc):
        """Collect source/sources/link strings for the Citations page. Skips the
        related_research reading list (kept separate)."""
        if isinstance(obj, dict):
            for k in ('sources', 'source', 'link'):
                v = obj.get(k)
                if isinstance(v, list):
                    for s in v:
                        if isinstance(s, dict) or (isinstance(s, str) and s.strip()):
                            acc.append(s)
                elif isinstance(v, dict) or (isinstance(v, str) and v.strip()):
                    acc.append(v)
            for vk, vv in obj.items():
                if vk == 'related_research':
                    continue
                if isinstance(vv, (dict, list)):
                    _section_sources(vv, acc)
        elif isinstance(obj, list):
            for it in obj:
                _section_sources(it, acc)

    def _story(toc_entries=None):
        story = []
        name = analysis.get('company_name', 'Unknown Subject')
        field = analysis.get('field', '')

        # ── Title
        story.append(_p("TECHNICAL DUE DILIGENCE REPORT", S["title"]))
        story.append(_p(f"<b>{name}</b>", S["heading"]))
        meta = f"Report Date: {datetime.now().strftime('%B %d, %Y')}"
        if field:
            meta = f"Field: {field} &nbsp;|&nbsp; " + meta
        story.append(_p(meta, S["body"]))
        story.append(_p(
            "<i>Informational brief to acquaint the reader with the technology and "
            "frame diligence. It contains no investment recommendation.</i>", S["small"]))
        story.append(Spacer(1, 0.1 * inch))

        if analysis.get('partial'):
            story.append(_p(
                f"<b><font color='{WARN_ORANGE}'>&#9888; PARTIAL REPORT</font></b> &mdash; the run "
                f"did not fully complete ({analysis.get('partial_reason', 'unknown reason')}). "
                f"Sections below were assembled from the document reads and research gathered "
                f"before it ended; re-run for the full brief.", S["small"]))
            story.append(Spacer(1, 0.08 * inch))

        # ── 1. Executive Summary
        ex = analysis.get('executive_summary', {}) or {}
        story.append(_Anchor("exec"))
        story.append(_p("1. EXECUTIVE SUMMARY", S["heading"]))
        conf = (analysis.get('confidence_level') or 'N/A').upper()
        story.append(_p(
            f"<b>Confidence in the technical claim: "
            f"<font color='{_conf_color(conf)}'>{conf}</font></b> "
            f"<i>(an assessment of the claim/evidence — not an investment view)</i>",
            S["small"]))
        if ex.get('technology'):
            story.append(_p(f"<b>Technology:</b> {ex['technology']}", S["body"]))
        if ex.get('commercial_claim'):
            story.append(_p(f"<b>Commercial claim (per founder):</b> {ex['commercial_claim']}", S["body"]))
        if ex.get('main_diligence_concern'):
            story.append(_p(f"<b>Main diligence concern:</b> {ex['main_diligence_concern']}", S["body"]))
        if ex.get('confidence_note'):
            story.append(_p(f"<b>Why this confidence level:</b> {ex['confidence_note']}", S["small"]))
        story.append(Spacer(1, 0.08 * inch))
        story.append(_Commentary("c_exec", "Team Commentary — Executive Summary:"))
        story.append(PageBreak())

        # ── TOC
        story.extend(_toc(toc_entries))

        # ── 2. Technology Explanation
        te = analysis.get('technology_explanation', {}) or {}
        story.append(_Anchor("tech"))
        story.append(_p("2. TECHNOLOGY EXPLANATION", S["heading"]))
        story.append(_p("<i>For a technically literate reader who is not a specialist in this domain.</i>", S["small"]))
        if te.get('summary'):
            story.append(_p(te['summary'], S["body"]))
        terms = te.get('key_terms', []) or []
        if terms:
            story.append(_p("Key Terms", S["sub"]))
            for t in terms:
                story.append(_p(f"<b>{t.get('term', '')}</b> — {t.get('definition', '')}", S["small"]))
        story.append(Spacer(1, 0.08 * inch))
        story.append(_Commentary("c_tech", "Team Commentary — Technology Explanation:"))
        story.append(PageBreak())

        # ── 3. Claimed Product / Use Case
        cp = analysis.get('claimed_product', {}) or {}
        story.append(_Anchor("product"))
        story.append(_p("3. CLAIMED PRODUCT / USE CASE", S["heading"]))
        clar = (cp.get('clarity') or '').upper()
        if clar:
            story.append(_p(f"<b>Product clarity: <font color='{_clarity_color(clar)}'>{clar}</font></b>", S["small"]))
        if cp.get('what_they_are_building'):
            story.append(_p(cp['what_they_are_building'], S["body"]))
        if cp.get('notes'):
            story.append(_p(f"<i>{cp['notes']}</i>", S["small"]))
        story.append(Spacer(1, 0.08 * inch))
        story.append(_Commentary("c_product", "Team Commentary — Claimed Product:"))
        story.append(PageBreak())

        # ── 4. Theory-to-Product Bridge
        br = analysis.get('theory_to_product_bridge', {}) or {}
        story.append(_Anchor("bridge"))
        story.append(_p("4. THEORY-TO-PRODUCT BRIDGE", S["heading"]))
        story.append(_p("<i>How the science is meant to become a product — and where the gaps are.</i>", S["small"]))
        for key, label, color in (
            ("proven", "Proven (established by the science)", GOOD_GREEN),
            ("asserted", "Asserted (claimed but not yet shown)", WARN_ORANGE),
            ("missing", "Missing (absent but required for a product)", BAD_RED),
            ("must_be_demonstrated", "Must be demonstrated experimentally", ACCENT_BLUE),
        ):
            vals = br.get(key, []) or []
            if vals:
                story.append(_p(f"<font color='{color}'><b>{label}</b></font>", S["sub"]))
                story.extend(_bullets(vals, S["small"]))
        story.append(Spacer(1, 0.08 * inch))
        story.append(_Commentary("c_bridge", "Team Commentary — Theory-to-Product Bridge:"))
        story.append(PageBreak())

        # ── 5. Prior Work & Literature Map (+ Further Reading)
        story.append(_Anchor("prior"))
        story.append(_p("5. PRIOR WORK & LITERATURE MAP", S["heading"]))
        for pw in (analysis.get('prior_work_literature_map', []) or []):
            srcs = [s for s in (pw.get('sources') or []) if s]
            src_txt = f" <i>[{', '.join(_cite(s) for s in srcs[:2])}]</i>" if srcs else ""
            mat = f" <font color='{MUTED}'>({pw['maturity']})</font>" if pw.get('maturity') else ""
            bench = f"<br/><b>Benchmark it sets:</b> {pw['benchmark_it_sets']}" if pw.get('benchmark_it_sets') else ""
            diff = f"<br/><b>Differentiation:</b> {pw['differentiation']}" if pw.get('differentiation') else ""
            story.append(_p(
                f"<b>{pw.get('area', 'Area')}</b>{mat} — {pw.get('what_prior_work_does', '')}"
                f"{bench}{diff}{src_txt}", S["small"]))
            story.append(Spacer(1, 0.03 * inch))
        related = analysis.get('related_research', []) or []
        story.append(_p("Further Reading", S["sub"]))
        story.append(_p("<i>A curated reading list on the underlying science, separate from the Citations used here.</i>", S["small"]))
        if related:
            for i, paper in enumerate(related, 1):
                linked = _link(paper.get('title', 'Untitled'), paper.get('link', ''))
                meta_bits = " &middot; ".join([b for b in (paper.get('authors', ''), paper.get('venue_year', '')) if b])
                meta_line = f"<br/><font color='{MUTED}'>{meta_bits}</font>" if meta_bits else ""
                why = f"<br/><i>{paper['why_relevant']}</i>" if paper.get('why_relevant') else ""
                story.append(_p(f"<b>{i}. {linked}</b>{meta_line}{why}", S["small"]))
                story.append(Spacer(1, 0.03 * inch))
        else:
            story.append(_p("No further reading was surfaced for this subject.", S["small"]))
        story.append(Spacer(1, 0.08 * inch))
        story.append(_Commentary("c_prior", "Team Commentary — Prior Work & Literature:"))
        story.append(PageBreak())

        # ── 6. Benchmarking Framework
        story.append(_Anchor("bench"))
        story.append(_p("6. BENCHMARKING FRAMEWORK", S["heading"]))
        story.append(_p("<i>The metrics that matter in this domain and what 'good' looks like.</i>", S["small"]))
        bench_rows = analysis.get('benchmarking_framework', []) or []
        if bench_rows:
            for b in bench_rows:
                src = b.get('source', '')
                src_txt = f" <i>[{_cite(src)}]</i>" if src else ""
                good = f"<br/><b>What good looks like:</b> {b['what_good_looks_like']}" if b.get('what_good_looks_like') else ""
                story.append(_p(
                    f"<b>{b.get('metric', 'Metric')}</b> — {b.get('why_it_matters', '')}{good}{src_txt}",
                    S["small"]))
                story.append(Spacer(1, 0.03 * inch))
        else:
            story.append(_p("No domain benchmarks were identified — request the relevant figures of merit from the company.", S["small"]))
        story.append(Spacer(1, 0.08 * inch))
        story.append(_Commentary("c_bench", "Team Commentary — Benchmarking:"))
        story.append(PageBreak())

        # ── 7. Commercial Implications
        ci = analysis.get('commercial_implications', {}) or {}
        story.append(_Anchor("commercial"))
        story.append(_p("7. COMMERCIAL IMPLICATIONS", S["heading"]))
        if ci.get('what_it_could_become'):
            story.append(_p(ci['what_it_could_become'], S["body"]))
        if ci.get('first_markets'):
            story.append(_p("<b>Likely first markets:</b> " + "; ".join(ci['first_markets']), S["small"]))
        if ci.get('customers'):
            story.append(_p("<b>Likely customers:</b> " + "; ".join(ci['customers']), S["small"]))
        if ci.get('adoption_drivers'):
            story.append(_p("<b>Adoption drivers:</b> " + "; ".join(ci['adoption_drivers']), S["small"]))
        if ci.get('why_it_matters_economically'):
            story.append(_p(f"<b>Why it matters economically:</b> {ci['why_it_matters_economically']}", S["small"]))
        cc = ci.get('comparable_companies', []) or []
        if cc:
            story.append(_p("Comparable Companies", S["sub"]))
            for c in cc:
                sc = f" ({c['scale_or_funding']})" if c.get('scale_or_funding') else ""
                story.append(_p(f"<b>{c.get('name', '')}</b>{sc}: {c.get('context', '')}", S["small"]))
        story.append(Spacer(1, 0.08 * inch))
        story.append(_Commentary("c_commercial", "Team Commentary — Commercial Implications:"))
        story.append(PageBreak())

        # ── 8. Manufacturing Scale-Up & Commercialization Risk
        mf = analysis.get('manufacturing_scaleup_risk', {}) or {}
        story.append(_Anchor("manufacturing"))
        story.append(_p("8. MANUFACTURING SCALE-UP & COMMERCIALIZATION RISK", S["heading"]))
        if mf.get('summary'):
            story.append(_p(mf['summary'], S["body"]))
        risks = mf.get('risks', []) or []
        if risks:
            story.append(_p("Key Risks", S["sub"]))
            for r in risks:
                sev = (r.get('severity') or '').upper()
                badge = f"<font color='{_risk_color(sev)}'>[{sev}]</font> " if sev else ""
                area = f"<b>{r['area']}:</b> " if r.get('area') else ""
                story.append(_p(f"{badge}{area}{r.get('risk', '')}", S["small"]))
        story.append(Spacer(1, 0.08 * inch))
        story.append(_Commentary("c_manufacturing", "Team Commentary — Manufacturing & Risk:"))
        story.append(PageBreak())

        # ── 9. Key Diligence Questions / Data Requests
        kq = analysis.get('key_diligence_questions', {}) or {}
        story.append(_Anchor("diligence"))
        story.append(_p("9. KEY DILIGENCE QUESTIONS / DATA REQUESTS", S["heading"]))
        story.append(_p("<i>Practical, answerable requests to send the company next.</i>", S["small"]))
        _DQ = [
            ("prototype_data", "Prototype / measured data"),
            ("simulations", "Simulations & models"),
            ("benchmark_comparisons", "Benchmark comparisons"),
            ("third_party_validation", "Third-party validation"),
            ("patent_filings", "Patents / freedom-to-operate"),
            ("manufacturing_process", "Manufacturing & process"),
            ("customer_pilot_evidence", "Customer / pilot evidence"),
            ("supplementary_datasets", "Supplementary datasets"),
        ]
        any_q = False
        for key, label in _DQ:
            vals = kq.get(key, []) or []
            if vals:
                any_q = True
                story.append(_p(label, S["sub"]))
                story.extend(_bullets(vals, S["small"]))
        if not any_q:
            story.append(_p("No specific data requests were generated.", S["small"]))
        story.append(Spacer(1, 0.1 * inch))
        story.append(_Commentary("c_diligence", "Team Commentary — Diligence Questions & Answers:", h=1.8 * inch))

        # ── Methodology (neutral, no recommendation)
        story.append(Spacer(1, 0.12 * inch))
        story.append(_p(
            f"<i><b>Methodology:</b> Multi-pass technical analysis — full reads of the "
            f"founder's document(s), web research for prior work / datasets / benchmarks / "
            f"market, and synthesis. {_count_unique_sources(analysis)} unique external sources "
            f"consulted. This brief is informational and contains no investment recommendation.</i>"
            f"<br/><b>Generated:</b> {datetime.now().strftime('%B %d, %Y at %H:%M:%S')}",
            S["small"]))

        # ── Citations
        story.append(PageBreak())
        story.append(_Anchor("citations"))
        story.append(_p("CITATIONS", S["heading"]))
        story.append(_p("<i>Sources used to build this brief (the Further Reading list above is separate).</i>", S["small"]))
        acc = []
        for key in ('prior_work_literature_map', 'benchmarking_framework',
                    'theory_to_product_bridge', 'commercial_implications',
                    'manufacturing_scaleup_risk', 'technology_explanation',
                    'claimed_product', 'executive_summary'):
            _section_sources(analysis.get(key), acc)
        acc.extend(analysis.get('_research_sources', []) or [])
        def _src_key(s):
            if isinstance(s, dict):
                return str(s.get('url') or s.get('link') or s.get('title') or '').strip().lower()
            return str(s).strip().lower()
        seen, unique = set(), []
        for s in acc:
            k = _src_key(s)
            if k and k not in seen:
                seen.add(k)
                unique.append(s)
        src_st = ParagraphStyle('TSrc', parent=S['small'], fontSize=8, leading=11,
                                spaceAfter=2, fontName=FONT, textColor=colors.HexColor('#333333'))
        if unique:
            for s in unique:
                story.append(_p(f"- {_cite(s)}", src_st))
        else:
            story.append(_p("No external citations were recorded.", src_st))

        # Verified live links harvested straight from web search — real URLs WITH
        # titles (the reliable, named citation layer beneath the model's own).
        web_sources = analysis.get('web_sources') or []
        if web_sources:
            story.append(Spacer(1, 0.06 * inch))
            story.append(_p("<b>Additional web-search links</b> <i>(captured directly from web search; supplementary, not necessarily tied to a specific claim)</i>:", src_st))
            seen_w = set()
            for ws in web_sources[:10]:
                if not isinstance(ws, dict):
                    continue
                u = str(ws.get('url') or '').strip()
                if not u or u in seen_w:
                    continue
                seen_w.add(u)
                title = str(ws.get('title') or u).strip()
                story.append(_p(f"- {_link(title, u)}", src_st))

        docs = analysis.get('_doc_filenames', []) or []
        if docs:
            story.append(Spacer(1, 0.06 * inch))
            story.append(_p("<b>Documents analyzed:</b> " + ", ".join(docs), src_st))
        story.append(Spacer(1, 0.05 * inch))
        story.append(_p(f"<b>Total unique external sources:</b> {_count_unique_sources(analysis)}", src_st))

        return story

    _args = dict(pagesize=letter, topMargin=0.65 * inch, bottomMargin=0.65 * inch,
                 leftMargin=0.7 * inch, rightMargin=0.7 * inch)
    SimpleDocTemplate(io.BytesIO(), **_args).build(_story())
    captured = dict(toc_tracker)
    toc_tracker.clear()
    SimpleDocTemplate(output_path, **_args).build(_story(toc_entries=captured))
