"""
ddr_report.py
=============
PDF report generation for Due Diligence Reports (DDR).
Integrated into the VoLo Engine from the standalone DDR V2 tool.

Generates a professional PDF with:
  - Company overview & status flags
  - Table of contents with accurate page numbers (two-pass build)
  - Competitive landscape
  - Claims assessment
  - Unverified claims with investigation steps
  - Outcome magnitude scenarios
  - Sources page
  - Fillable team commentary fields
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
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    Flowable,
)
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily


# ── Fonts (bundled Unicode TTF so math/Greek/symbols render; no tofu boxes) ───
# Mirrors the tech-DDR renderer: register a DejaVuSans family and sanitize any
# glyph the active font cannot draw, so emojis / special characters never show
# up as black boxes (□) in the PDF.
_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONT = "Helvetica"
FONT_B = "Helvetica-Bold"
FONT_I = "Helvetica-Oblique"
FONT_BI = "Helvetica-BoldOblique"
_SUPPORTED = None  # set of codepoints the active font can render (None = Helvetica/Latin-1)


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
        print(f"[DDR] DejaVu font registration failed, using Helvetica: {ex}", flush=True)
        _SUPPORTED = None


_register_fonts()

# Transliterations used ONLY when the active font lacks the glyph.
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

# Always removed even if the font CAN draw them — tofu/box/replacement glyphs.
_FORCE_STRIP = {"�", "■", "□", "▪", "▫", "◼", "◻", "◾", "◽"}


def _font_safe(s: str) -> str:
    """Replace any character the active font cannot render with a transliteration
    or NFKD-ASCII fallback, so the PDF never shows a tofu box. A small set of
    box/replacement glyphs is dropped unconditionally. Emojis (no glyph in
    DejaVu) are transliterated away or dropped rather than rendered as boxes."""
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

# ── Shared Colors ────────────────────────────────────────────────────────────

VOLO_GREEN    = "#2d5f3f"
VOLO_LIGHT    = "#3a7d52"
VOLO_PALE     = "#eef7f1"
ACCENT_ORANGE = "#e07b39"
ACCENT_BLUE   = "#3a6ea8"
ACCENT_PURPLE = "#7b5ea7"
GRID_COLOR    = "#d4e6da"
TEXT_DARK     = "#1a1a1a"
TEXT_MID      = "#4a4a4a"


# ── Escaping ─────────────────────────────────────────────────────────────────

def _esc(text) -> str:
    if not isinstance(text, str):
        text = str(text)
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("$", "&#36;"))


_SAFE_TAG_RE = re.compile(
    r'</?(?:b|i|u|br|br/|super|sub|font|a|para|seq|seqreset|onDraw|index|img)(?:\s[^>]*)?>',
    re.IGNORECASE,
)

_ENTITY_RE = re.compile(r'&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);')


def _esc_preserving_entities(text: str) -> str:
    parts = _ENTITY_RE.split(text)
    entities = _ENTITY_RE.findall(text)
    escaped = [_esc(p) for p in parts]
    result = []
    for i, part in enumerate(escaped):
        result.append(part)
        if i < len(entities):
            result.append(entities[i])
    return "".join(result)


def _p(text, style) -> Paragraph:
    if not isinstance(text, str):
        text = str(text)
    text = _font_safe(text)  # strip/transliterate unrenderable glyphs so no tofu boxes
    parts = _SAFE_TAG_RE.split(text)
    tags = _SAFE_TAG_RE.findall(text)
    escaped_parts = [_esc_preserving_entities(p) for p in parts]
    result = []
    for i, part in enumerate(escaped_parts):
        result.append(part)
        if i < len(tags):
            result.append(tags[i])
    return Paragraph("".join(result), style)


def _dollar(amount_usd: float) -> str:
    if amount_usd >= 1e9:
        return f"&#36;{amount_usd / 1e9:.1f}B"
    elif amount_usd > 0:
        return f"&#36;{amount_usd / 1e6:.0f}M"
    return "Not quantified"


# ── Citations ────────────────────────────────────────────────────────────────

def _cite(src) -> str:
    """Render one citation as reportlab inline markup.

    Accepts a legacy plain string (source name) or a dict
    ``{title, url, publisher}``. Produces a clickable link — with the title as
    the visible text — when a real http(s) URL is present; otherwise just the
    title/name, which stays findable even if a link is missing or dead (the
    safeguard). The visible label is returned RAW so the caller's ``_p()``
    escapes it; only the href URL is sanitized here.
    """
    if isinstance(src, dict):
        title = str(src.get("title") or src.get("publisher") or src.get("name") or "Source").strip()
        pub = str(src.get("publisher") or "").strip()
        label = title if (not pub or pub.lower() in title.lower()) else f"{title} ({pub})"
        url = str(src.get("url") or "").strip()
    else:
        label = str(src).strip()
        url = ""
    if url and re.match(r'^https?://\S+$', url):
        safe_url = url.replace("&", "&amp;").replace('"', "%22").replace("<", "").replace(">", "")
        return f'<a href="{safe_url}" color="#3a6ea8"><u>{label}</u></a>'
    return label


def _cite_list(sources, limit: int = 3) -> str:
    """Comma-join up to ``limit`` citations (clickable where a real URL exists)."""
    items = [_cite(s) for s in (sources or [])[:limit] if s]
    return ", ".join(i for i in items if i)


def _count_unique_sources(analysis: dict) -> int:
    """Single deduped unique-source count reported IDENTICALLY in the methodology
    line and the sources appendix (fix 31). Counts every claim/section source
    plus the harvested web_sources, deduped by URL/title/publisher — never the
    model's self-reported ``sources_consulted``, which can disagree."""
    def _key(s):
        if isinstance(s, dict):
            return (s.get("url") or s.get("title") or s.get("publisher") or "").strip().lower()
        return str(s).strip().lower()
    uniq: set = set()

    def _walk(obj):
        if isinstance(obj, dict):
            for k in ("sources", "source", "current_best_source"):
                val = obj.get(k)
                if isinstance(val, list):
                    for s in val:
                        if isinstance(s, dict) or (isinstance(s, str) and s.strip()):
                            kk = _key(s)
                            if kk:
                                uniq.add(kk)
                elif isinstance(val, dict) or (isinstance(val, str) and val.strip()):
                    kk = _key(val)
                    if kk:
                        uniq.add(kk)
            for k in ("source_note", "note"):  # match the appendix, which lists these too
                v = obj.get(k)
                if isinstance(v, str) and v.strip():
                    uniq.add(v.strip().lower())
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    _walk(v)
        elif isinstance(obj, list):
            for it in obj:
                _walk(it)

    for sec in ("claims", "unverified_claims", "competitive_landscape",
                "status_flags", "outcome_magnitude"):
        _walk(analysis.get(sec))
    # Cap web at the same 10 shown in the appendix so the count never exceeds the
    # enumerated list.
    for ws in (analysis.get("web_sources") or [])[:10]:
        u = (ws.get("url") or "").strip().lower() if isinstance(ws, dict) else ""
        if u:
            uniq.add(u)
    return len(uniq)


# ── PDF Styles ───────────────────────────────────────────────────────────────

def _build_styles():
    base = getSampleStyleSheet()
    # Point every inherited base style at the registered DejaVu family so glyphs
    # the sanitizer keeps (Greek/math/symbols) actually render instead of tofu.
    _fmap = {'Helvetica': FONT, 'Helvetica-Bold': FONT_B,
             'Helvetica-Oblique': FONT_I, 'Helvetica-BoldOblique': FONT_BI}
    for _sn in list(base.byName.keys()):
        _st = base[_sn]
        _fn = getattr(_st, 'fontName', '') or ''
        if _fn in _fmap:
            _st.fontName = _fmap[_fn]
    return {
        "title": ParagraphStyle(
            'DDRTitle', parent=base['Heading1'],
            fontSize=24, textColor=colors.HexColor('#2d5f3f'),
            spaceAfter=16, alignment=TA_CENTER, fontName=FONT_B,
        ),
        "heading": ParagraphStyle(
            'DDRHeading', parent=base['Heading2'],
            fontSize=15, textColor=colors.HexColor('#2d5f3f'),
            spaceAfter=10, spaceBefore=16, fontName=FONT_B,
        ),
        "subheading": ParagraphStyle(
            'DDRSubheading', parent=base['Heading3'],
            fontSize=12, textColor=colors.HexColor('#1a472a'),
            spaceAfter=6, spaceBefore=10, fontName=FONT_B,
        ),
        "body": ParagraphStyle(
            'DDRBody', parent=base['BodyText'],
            fontSize=10, leading=14, spaceAfter=8, alignment=TA_JUSTIFY,
        ),
        "body_small": ParagraphStyle(
            'DDRBodySmall', parent=base['BodyText'],
            fontSize=9, leading=12, spaceAfter=6, alignment=TA_JUSTIFY,
        ),
        "alert": ParagraphStyle(
            'DDRAlert', parent=base['BodyText'],
            fontSize=10, leading=14, spaceAfter=8,
        ),
        "flag": ParagraphStyle(
            'DDRFlag', parent=base['BodyText'],
            fontSize=10, leading=14, spaceAfter=8,
        ),
        "verified": ParagraphStyle(
            'DDRVerified', parent=base['BodyText'],
            fontSize=10, leading=14, spaceAfter=8,
        ),
    }


# ── PDF Generation ───────────────────────────────────────────────────────────

def generate_report_pdf(analysis: dict, output_path: str):
    """
    Generate the complete DDR PDF report with a table of contents
    with accurate page numbers (two-pass build).
    """
    S = _build_styles()
    toc_tracker = {}

    _TOC_ORDER = [
        ("overview", "Company Overview"),
        ("competitive", "Competitive Landscape"),
        ("claims", "Claims Assessment"),
        ("unverified", "Unverified Claims"),
        ("outcome", "Outcome Magnitude"),
        ("conclusion", "Conclusion"),
        ("sources", "Sources"),
    ]

    class _Anchor(Flowable):
        def __init__(self, key):
            Flowable.__init__(self)
            self.key = key
            self.width = 0
            self.height = 0
        def draw(self):
            toc_tracker[self.key] = self.canv.getPageNumber()

    class _CommentaryField(Flowable):
        def __init__(self, field_name, label="Team Commentary:",
                     field_width=6.5*inch, field_height=1.5*inch):
            Flowable.__init__(self)
            self.field_name = field_name
            self.label = label
            self.field_width = field_width
            self.field_height = field_height
            self.width = field_width
            self.height = field_height + 16
        def draw(self):
            canv = self.canv
            canv.setStrokeColor(colors.HexColor('#d4e6da'))
            canv.setLineWidth(0.5)
            canv.line(0, self.height, self.field_width, self.height)
            canv.setFont(FONT_B, 8)
            canv.setFillColor(colors.HexColor(VOLO_GREEN))
            canv.drawString(2, self.field_height + 3, _font_safe(self.label))
            canv.acroForm.textfield(
                name=self.field_name, tooltip=self.label,
                x=0, y=0, width=self.field_width, height=self.field_height,
                borderStyle='inset',
                borderColor=colors.HexColor('#c8dcc8'),
                fillColor=colors.HexColor('#f8fbf9'),
                textColor=colors.black, forceBorder=True, relative=True,
                fieldFlags='multiline',
                # CRITICAL: reportlab's internal default maxlen=100 caps
                # the field at 100 characters — the reason users couldn't
                # type more than about one line of text. Explicitly setting
                # a very large limit removes the cap in practice.
                maxlen=100000,
                fontSize=9,
            )

    def _toc_flowables(entries=None):
        items = []
        toc_title = ParagraphStyle(
            'DDRTOCTitle', parent=S['title'],
            fontSize=20, spaceAfter=16, alignment=TA_CENTER,
        )
        items.append(_p("TABLE OF CONTENTS", toc_title))
        items.append(Spacer(1, 0.15 * inch))
        items.append(Table(
            [['']],
            colWidths=[6.5 * inch],
            style=TableStyle([
                ('LINEBELOW', (0, 0), (-1, -1), 1.5,
                 colors.HexColor(VOLO_GREEN)),
            ]),
        ))
        items.append(Spacer(1, 0.25 * inch))
        if entries:
            toc_name = ParagraphStyle(
                'DDRTOCName', parent=S['body'], fontSize=11, leading=18,
            )
            toc_pg = ParagraphStyle(
                'DDRTOCPg', parent=S['body'], fontSize=11, leading=18,
                alignment=TA_CENTER, fontName=FONT_B,
                textColor=colors.HexColor(VOLO_GREEN),
            )
            rows = []
            for key, label in _TOC_ORDER:
                if key in entries:
                    rows.append([
                        Paragraph(_font_safe(label), toc_name),
                        Paragraph(_font_safe(str(entries[key])), toc_pg),
                    ])
            if rows:
                tbl = Table(rows, colWidths=[5.8 * inch, 0.7 * inch])
                tbl.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                    ('LINEBELOW', (0, 0), (-1, -1), 0.4,
                     colors.HexColor('#e0e8e2')),
                ]))
                items.append(tbl)
        else:
            items.append(Spacer(1, 5 * inch))
        items.append(PageBreak())
        return items

    def _make_story(toc_entries=None):
        story = []

        # ── PAGE 1: TITLE, OVERVIEW & STATUS FLAGS
        story.append(_p("DUE DILIGENCE REPORT", S["title"]))
        story.append(Spacer(1, 0.1 * inch))

        company = analysis.get('company_name', 'Unknown')
        industry = analysis.get('industry', 'Unknown')

        story.append(_p(f"<b>{company}</b>", S["heading"]))
        story.append(_p(
            f"Industry: {industry} &nbsp;|&nbsp; "
            f"Report Date: {datetime.now().strftime('%B %d, %Y')}",
            S["body"],
        ))
        story.append(Spacer(1, 0.15 * inch))

        overview = analysis.get('company_overview', {})
        story.append(_Anchor("overview"))
        story.append(_p("COMPANY OVERVIEW", S["heading"]))
        story.append(_p(overview.get('description', 'Not available'), S["body"]))
        story.append(_p(f"<b>Stage:</b> {overview.get('stage', 'Unknown')}", S["body"]))

        status = analysis.get('status_flags', {})
        overall = status.get('overall_status', 'UNKNOWN')

        if overall in ['DISTRESSED', 'CRITICAL']:
            story.append(Spacer(1, 0.08 * inch))
            story.append(_p(
                f"<b>Company Status: {overall}</b> — {status.get('notes', '')}",
                S["alert"],
            ))

        bank = status.get('bankruptcy_insolvency', {})
        fund = status.get('recent_funding', {})
        ip = status.get('ip_status', {})
        lit = status.get('active_litigation', {})

        flags = []
        bank_status = bank.get('status', 'UNKNOWN')
        if bank_status not in ['NONE FOUND', 'UNKNOWN', 'ACTIVE'] and bank.get('details'):
            flags.append(f"Bankruptcy ({bank_status}): {bank['details']}")
        fund_outcome = fund.get('outcome', 'UNKNOWN')
        if fund_outcome == 'FAILED' and fund.get('failure_reasons'):
            flags.append(f"Failed funding: {fund.get('failure_reasons', 'Not disclosed')}")
        ip_status = ip.get('status', 'UNKNOWN')
        if ip_status in ['DISPUTED', 'ENCUMBERED'] and ip.get('details'):
            flags.append(f"IP {ip_status}: {ip['details']}")
        lawsuits = lit.get('lawsuits', [])
        if lawsuits:
            flags.append(f"Litigation: {'; '.join(lawsuits[:2])}")
        if flags:
            story.append(_p(
                "<b>Flags:</b> " + " | ".join(flags), S["body_small"],
            ))
        elif overall not in ['DISTRESSED', 'CRITICAL'] and status.get('notes'):
            story.append(_p(f"<b>Background:</b> {status['notes']}", S["body_small"]))

        story.append(Spacer(1, 0.15 * inch))
        story.append(_CommentaryField(
            "commentary_overview", "Team Commentary \u2014 Company Overview:"))
        story.append(PageBreak())

        # ── TABLE OF CONTENTS PAGE
        story.extend(_toc_flowables(toc_entries))

        # ── COMPETITIVE LANDSCAPE
        comp = analysis.get('competitive_landscape', {})
        story.append(_Anchor("competitive"))
        story.append(_p("COMPETITIVE LANDSCAPE", S["heading"]))
        story.append(_p(comp.get('positioning_summary', ''), S["body"]))
        story.append(Spacer(1, 0.1 * inch))

        peers = comp.get('peer_competitors', [])
        if peers:
            story.append(_p("Peer-Stage Competitors", S["subheading"]))
            for p in peers:
                funding = p.get('funding_raised_usd') or 0
                funding_str = _dollar(funding) + " raised" if funding else "Funding unknown"
                story.append(_p(
                    f"<b>{p.get('name', 'Unknown')}</b> "
                    f"({p.get('stage', '?')} — {funding_str}): "
                    f"{p.get('description', '')}"
                    + (f" <i>[{_cite_list(p['sources'], 2)}]</i>" if p.get('sources') else ""),
                    S["body_small"],
                ))
                story.append(Spacer(1, 0.04 * inch))

        leaders = comp.get('market_leaders', [])
        if leaders:
            story.append(Spacer(1, 0.06 * inch))
            story.append(_p("Market Leaders &amp; Incumbents", S["subheading"]))
            for ldr in leaders:
                ldr_pos = ldr.get('market_position', '')
                ldr_val = ldr.get('valuation_or_revenue', '')
                ldr_meta = ""
                if ldr_pos and ldr_val:
                    ldr_meta = f" — {ldr_pos} ({ldr_val})"
                elif ldr_pos:
                    ldr_meta = f" — {ldr_pos}"
                elif ldr_val:
                    ldr_meta = f" ({ldr_val})"
                story.append(_p(
                    f"<b>{ldr.get('name', 'Unknown')}</b>{ldr_meta}: "
                    f"{ldr.get('description', '')}"
                    + (f" <i>[{_cite_list(ldr['sources'], 2)}]</i>" if ldr.get('sources') else ""),
                    S["body_small"],
                ))
                story.append(Spacer(1, 0.04 * inch))

        risks = comp.get('competitive_risks', [])
        acquirers = comp.get('potential_acquirers', [])
        if risks:
            story.append(Spacer(1, 0.06 * inch))
            story.append(_p(
                "<b>Competitive Risks:</b> " + " · ".join(risks), S["body_small"],
            ))
        if acquirers:
            story.append(_p(
                "<b>Potential Acquirers:</b> " + " · ".join(acquirers), S["body_small"],
            ))

        story.append(Spacer(1, 0.15 * inch))
        story.append(_CommentaryField(
            "commentary_competitive", "Team Commentary \u2014 Competitive Landscape:"))
        story.append(PageBreak())

        # ── CLAIMS ASSESSMENT
        claims = analysis.get('claims', [])
        story.append(_Anchor("claims"))
        story.append(_p("CLAIMS ASSESSMENT", S["heading"]))
        story.append(_p(
            "<i>Quick-scan status of all technology and market claims.</i>",
            S["body_small"],
        ))
        story.append(Spacer(1, 0.08 * inch))

        for cl in claims:
            cl_type = cl.get('type', 'OTHER')[:4].upper()
            v_status = cl.get('verification_status', 'company-reported')
            # Map the 5-level verification taxonomy onto the existing 3 render
            # styles, with back-compat for the legacy binary values saved in
            # older reports. green = independently verified; caution = supported
            # by literature/secondary evidence (or legacy "partially verified");
            # alert = company-reported / unsupported / legacy "unverified".
            _vs = (v_status or '').strip().lower()
            if _vs in ('independently verified', 'verified'):
                use_style = S["verified"]
            elif _vs in ('supported by peer-reviewed literature',
                         'supported by credible secondary evidence',
                         'partially verified'):
                use_style = S["flag"]
            else:  # company-reported, unsupported or conflicting, unverified, unknown
                use_style = S["alert"]
            text = (
                f"<b>[{cl_type}] {cl.get('claim', 'N/A')}</b><br/>"
                f"{cl.get('source_label', v_status)}"
            )
            if cl.get('sources'):
                text += f" — <i>{_cite_list(cl['sources'], 2)}</i>"
            story.append(_p(text, use_style))
            story.append(Spacer(1, 0.04 * inch))

        story.append(Spacer(1, 0.1 * inch))
        story.append(_CommentaryField(
            "commentary_claims", "Team Commentary \u2014 Claims Assessment:"))

        # ── UNVERIFIED CLAIMS (CRITICAL + HIGH only)
        unverified = analysis.get('unverified_claims', [])
        priority_order = ['CRITICAL', 'HIGH']
        uv_filtered = sorted(
            [uc for uc in unverified if uc.get('priority', 'LOW') in priority_order],
            key=lambda c: priority_order.index(c.get('priority', 'HIGH'))
            if c.get('priority', 'HIGH') in priority_order else 1,
        )

        if uv_filtered:
            story.append(Spacer(1, 0.12 * inch))
            story.append(_Anchor("unverified"))
            story.append(_p("UNVERIFIED CLAIMS — Investigation &amp; Outcomes", S["heading"]))
            story.append(_p(
                "<i>Only CRITICAL and HIGH priority claims shown. Each includes "
                "investigation steps and potential outcome if verified.</i>",
                S["body_small"],
            ))
            story.append(Spacer(1, 0.06 * inch))

            for idx, uc in enumerate(uv_filtered, 1):
                priority = uc.get('priority', 'HIGH')
                outcome = uc.get('outcome_if_true') or {}
                mkt_usd = outcome.get('market_opportunity_usd') or 0
                mkt_str = _dollar(mkt_usd)
                use_style = S["alert"] if priority == 'CRITICAL' else S["flag"]

                story.append(_p(
                    f"<b>#{idx} [{priority}] {uc.get('claim', 'Not specified')}</b><br/>"
                    f"<b>Why Unverified:</b> {uc.get('why_unverified', 'No independent verification')}",
                    use_style,
                ))

                steps = uc.get('investigation_steps', [])
                if steps:
                    step_parts = " | ".join(f"({j+1}) {s}" for j, s in enumerate(steps))
                    story.append(_p(f"<b>Steps:</b> {step_parts}", S["body_small"]))

                if outcome:
                    story.append(_p(
                        f"<b>If Verified:</b> {outcome.get('description', '')} "
                        f"— Opportunity: <b>{mkt_str}</b>",
                        S["body_small"],
                    ))
                    for cmp in outcome.get('comparable_companies', []):
                        val = cmp.get('comparable_valuation_usd') or 0
                        val_str = f" ({_dollar(val)})" if val else ""
                        story.append(_p(
                            f"-&gt; <b>{cmp.get('company', 'N/A')}</b>{val_str}: "
                            f"{cmp.get('context', '')}",
                            S["body_small"],
                        ))
                    if outcome.get('key_caveat'):
                        story.append(_p(
                            f"<i>Caveat: {outcome['key_caveat']}</i>",
                            S["body_small"],
                        ))

                story.append(Spacer(1, 0.06 * inch))
                story.append(_CommentaryField(
                    f"commentary_unverified_{idx}",
                    f"Team Response \u2014 Claim #{idx}:",
                    field_height=1.2*inch))
                story.append(Spacer(1, 0.1 * inch))

        story.append(PageBreak())

        # ── OUTCOME MAGNITUDE + CONCLUSION
        story.append(_Anchor("outcome"))
        story.append(_p("OUTCOME MAGNITUDE", S["heading"]))
        story.append(_p(
            "<i>If the major claims hold up, what could this company become?</i>",
            S["body_small"],
        ))
        story.append(Spacer(1, 0.12 * inch))

        magnitude = analysis.get('outcome_magnitude', {})

        if_all = magnitude.get('if_all_claims_verified', {})
        if if_all:
            story.append(_p("If All Major Claims Are Verified:", S["subheading"]))
            story.append(_p(if_all.get('description', 'Not available'), S["body"]))
            story.append(_p(if_all.get('framing', ''), S["body_small"]))
            mkt = if_all.get('addressable_market_usd') or 0
            share = if_all.get('realistic_market_share_pct') or 0
            details = f"<b>Market:</b> {_dollar(mkt)} &nbsp;|&nbsp; <b>Share:</b> {share}%"
            if if_all.get('comparable_companies'):
                details += f" &nbsp;|&nbsp; <b>Comps:</b> {', '.join(if_all['comparable_companies'])}"
            story.append(_p(details, S["body_small"]))
            story.append(Spacer(1, 0.12 * inch))

        if_core = magnitude.get('if_core_tech_only_verified', {})
        if if_core:
            story.append(_p("If Only Core Technology Is Verified:", S["subheading"]))
            story.append(_p(if_core.get('description', 'Not available'), S["body"]))
            story.append(_p(if_core.get('framing', ''), S["body_small"]))
            mkt = if_core.get('addressable_market_usd') or 0
            details = f"<b>Market:</b> {_dollar(mkt)}"
            if if_core.get('comparable_companies'):
                details += f" &nbsp;|&nbsp; <b>Comps:</b> {', '.join(if_core['comparable_companies'])}"
            story.append(_p(details, S["body_small"]))
            story.append(Spacer(1, 0.12 * inch))

        deps = magnitude.get('key_dependencies', [])
        if deps:
            story.append(_p("What Must Be Proven First:", S["subheading"]))
            for dep in deps:
                story.append(_p(f"- {dep}", S["body_small"]))

        story.append(Spacer(1, 0.15 * inch))
        story.append(_CommentaryField(
            "commentary_outcome", "Team Commentary \u2014 Outcome Assessment:"))
        story.append(Spacer(1, 0.2 * inch))

        # ── CONCLUSION
        story.append(_Anchor("conclusion"))
        story.append(_p("CONCLUSION", S["heading"]))

        critical_claims = [uc for uc in unverified if uc.get('priority') == 'CRITICAL']
        high_claims = [uc for uc in unverified if uc.get('priority') == 'HIGH']

        story.append(_p(
            f"This report identified <b>{len(unverified)} unverified claims</b> across "
            f"{company}'s pitch deck, of which <b>{len(critical_claims)} are critical</b> and "
            f"<b>{len(high_claims)} are high priority</b>.",
            S["body"],
        ))

        if critical_claims:
            story.append(_p("Critical Claims Requiring Immediate Investigation:", S["subheading"]))
            for uc in critical_claims:
                outcome = uc.get('outcome_if_true') or {}
                mkt_usd = outcome.get('market_opportunity_usd') or 0
                story.append(_p(
                    f"- <b>{uc.get('claim', 'N/A')}</b> — {_dollar(mkt_usd)}",
                    S["body_small"],
                ))

        if if_all.get('framing'):
            story.append(Spacer(1, 0.1 * inch))
            story.append(_p(if_all.get('framing', ''), S["body_small"]))

        story.append(Spacer(1, 0.15 * inch))
        story.append(_p(
            f"<i><b>Methodology:</b> Analysis based on {_count_unique_sources(analysis)} unique sources, "
            f"including web research, financial databases, and industry reports. "
            f"No investment recommendation is made.</i><br/>"
            f"<b>Generated:</b> {datetime.now().strftime('%B %d, %Y at %H:%M:%S')}",
            S["body_small"],
        ))

        story.append(Spacer(1, 0.15 * inch))
        story.append(_CommentaryField(
            "commentary_conclusion",
            "Team Commentary \u2014 Final Notes & Next Steps:",
            field_height=2.0*inch))

        # ── SOURCES PAGE
        story.append(PageBreak())
        story.append(_Anchor("sources"))
        story.append(_p("SOURCES", S["heading"]))

        src_style = ParagraphStyle(
            'DDRSrcItem', parent=S["body_small"],
            fontSize=8, leading=10, spaceAfter=1, spaceBefore=0,
            textColor=colors.HexColor('#333333'),
        )
        src_heading_style = ParagraphStyle(
            'DDRSrcHeading', parent=S["body_small"],
            fontSize=9, leading=12, spaceAfter=2, spaceBefore=6,
            fontName=FONT_B, textColor=colors.HexColor('#2d5f3f'),
        )

        section_sources = {}

        def _collect(obj, section_label):
            if isinstance(obj, dict):
                for key in ('sources', 'source', 'current_best_source'):
                    val = obj.get(key)
                    if isinstance(val, list):
                        for s in val:
                            if isinstance(s, dict) or (isinstance(s, str) and s.strip()):
                                section_sources.setdefault(section_label, []).append(s)
                    elif isinstance(val, dict) or (isinstance(val, str) and val.strip()):
                        section_sources.setdefault(section_label, []).append(val)
                for key in ('source_note', 'note'):
                    val = obj.get(key)
                    if isinstance(val, str) and val.strip():
                        section_sources.setdefault(section_label, []).append(val.strip())
                for v in obj.values():
                    if isinstance(v, (dict, list)):
                        _collect(v, section_label)
            elif isinstance(obj, list):
                for item in obj:
                    _collect(item, section_label)

        _collect(analysis.get('claims', []), 'Claims')
        _collect(analysis.get('unverified_claims', []), 'Unverified Claims')
        _collect(analysis.get('competitive_landscape', {}), 'Competitive Landscape')
        _collect(analysis.get('status_flags', {}), 'Status & Legal')
        _collect(analysis.get('outcome_magnitude', {}), 'Outcome Magnitude')

        def _src_key(s):
            if isinstance(s, dict):
                return (s.get("url") or s.get("title") or s.get("publisher") or "").strip().lower()
            return str(s).strip().lower()

        for section_label in ['Claims', 'Competitive Landscape', 'Status & Legal',
                              'Unverified Claims', 'Outcome Magnitude']:
            sources = section_sources.get(section_label, [])
            if not sources:
                continue
            seen = set()
            unique = []
            for s in sources:
                key = _src_key(s)
                if key and key not in seen:
                    seen.add(key)
                    unique.append(s)
            story.append(_p(
                f"<b>{section_label}:</b> " + " · ".join(_cite(s) for s in unique),
                src_heading_style,
            ))

        # Verified live links harvested directly from web search — guaranteed
        # real URLs (the reliable-link layer beneath the per-claim citations).
        web_sources = analysis.get('web_sources') or []
        if web_sources:
            story.append(Spacer(1, 0.08 * inch))
            story.append(_p(
                "<b>Additional web-search links</b> <i>(captured directly from web search; supplementary, not necessarily tied to a specific claim)</i>:",
                src_heading_style,
            ))
            seen_w = set()
            for ws in web_sources[:10]:
                u = (ws.get('url') or '').strip() if isinstance(ws, dict) else ''
                if not u or u in seen_w:
                    continue
                seen_w.add(u)
                story.append(_p("&#8226; " + _cite(ws), src_style))

        story.append(Spacer(1, 0.1 * inch))
        story.append(_p(
            f"<b>Total unique sources cited:</b> {_count_unique_sources(analysis)}",
            src_style,
        ))

        return story

    # ── Two-pass build for accurate TOC page numbers
    _doc_args = dict(
        pagesize=letter,
        topMargin=0.65 * inch, bottomMargin=0.65 * inch,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
    )

    # Pass 1: build to buffer, capturing page numbers
    doc1 = SimpleDocTemplate(io.BytesIO(), **_doc_args)
    doc1.build(_make_story())

    # Pass 2: build with populated TOC
    captured = dict(toc_tracker)
    toc_tracker.clear()
    doc2 = SimpleDocTemplate(output_path, **_doc_args)
    doc2.build(_make_story(toc_entries=captured))
