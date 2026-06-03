"""
tech_ddr_report.py
==================
PDF generation for the **Technical / Deep-Tech Due Diligence Report**.

Self-contained (does not import from ddr_report.py) so the standard DDR PDF
path is left completely untouched. Sections:

  - Title + Innovation Summary (with an honest hypothesis assessment)
  - Technology Explainer (plain-language + technical depth)
  - How It Works
  - Novelty vs. Prior Work
  - Evidence & Data Sources
  - Commercial Implications
  - Manufacturing Scale-Up & Commercialization Risk
  - Claims Assessment (retained IC rigor)
  - Competitive Landscape (retained IC rigor)
  - Related Research / Further Reading (curated reading list — DISTINCT from Citations)
  - Conclusion
  - Citations (provenance of everything the analysis used)

Plus fillable team-commentary fields throughout.
"""

import io
import re
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Flowable,
)
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY

# ── Colors ───────────────────────────────────────────────────────────────────

VOLO_GREEN = "#2d5f3f"
ACCENT_BLUE = "#3a6ea8"
GOOD_GREEN = "#1f7a4d"
WARN_ORANGE = "#c8721f"
BAD_RED = "#b3402f"
MUTED = "#666666"


# ── Escaping (mirrors ddr_report's literal markup handling) ──────────────────

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
    """Paragraph that escapes free text but preserves a safe subset of tags
    (so <b>, <i>, <a href>, <br/> etc. render)."""
    if not isinstance(text, str):
        text = str(text)
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
    """Normalize a URL/DOI into a safe href, or '' if it isn't linkable."""
    u = (url or "").strip()
    if not u:
        return ""
    if u.lower().startswith("doi:"):
        u = u[4:].strip()
    if u.startswith("10.") and "/" in u:  # bare DOI
        u = "https://doi.org/" + u
    if not (u.startswith("http://") or u.startswith("https://")):
        return ""
    # The href lives inside the <a ...> tag which _p keeps verbatim, so escape
    # ampersands here and drop characters that would break the tag.
    return u.replace("&", "&amp;").replace('"', "%22").replace("<", "").replace(">", "")


def _link(text: str, url: str) -> str:
    """Return an <a> link if the URL is valid, otherwise the bare text."""
    href = _safe_href(url)
    if href:
        return f'<a href="{href}" color="{ACCENT_BLUE}"><u>{text}</u></a>'
    return text


def _sev_color(level: str) -> str:
    lv = (level or "").upper()
    if lv in ("HIGH", "WEAK", "DIFFERS", "CRITICAL"):
        return BAD_RED
    if lv in ("MEDIUM", "MODERATE", "PARTIALLY SUPPORTED", "PARTIALLY VERIFIED", "UNVERIFIABLE"):
        return WARN_ORANGE
    if lv in ("LOW", "STRONG", "MATCHES", "EXCEEDS", "VERIFIED"):
        return GOOD_GREEN
    return MUTED


# ── Styles ───────────────────────────────────────────────────────────────────

def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle('TTitle', parent=base['Heading1'], fontSize=23,
                                textColor=colors.HexColor(VOLO_GREEN), spaceAfter=14,
                                alignment=TA_CENTER, fontName='Helvetica-Bold'),
        "heading": ParagraphStyle('THeading', parent=base['Heading2'], fontSize=15,
                                  textColor=colors.HexColor(VOLO_GREEN), spaceAfter=9,
                                  spaceBefore=15, fontName='Helvetica-Bold'),
        "sub": ParagraphStyle('TSub', parent=base['Heading3'], fontSize=12,
                              textColor=colors.HexColor('#1a472a'), spaceAfter=5,
                              spaceBefore=9, fontName='Helvetica-Bold'),
        "body": ParagraphStyle('TBody', parent=base['BodyText'], fontSize=10,
                               leading=14, spaceAfter=8, alignment=TA_JUSTIFY),
        "small": ParagraphStyle('TSmall', parent=base['BodyText'], fontSize=9,
                                leading=12, spaceAfter=6, alignment=TA_JUSTIFY),
    }


# ── PDF build ────────────────────────────────────────────────────────────────

_TOC_ORDER = [
    ("summary", "Innovation Summary"),
    ("explainer", "Technology Explainer"),
    ("howitworks", "How It Works"),
    ("novelty", "Novelty vs. Prior Work"),
    ("evidence", "Evidence & Data Sources"),
    ("commercial", "Commercial Implications"),
    ("manufacturing", "Manufacturing Scale-Up & Risk"),
    ("claims", "Claims Assessment"),
    ("competitive", "Competitive Landscape"),
    ("related", "Related Research / Further Reading"),
    ("conclusion", "Conclusion"),
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
        def __init__(self, name, label, h=1.3 * inch):
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
            c.setFont('Helvetica-Bold', 8)
            c.setFillColor(colors.HexColor(VOLO_GREEN))
            c.drawString(2, self.fh + 3, self.label)
            c.acroForm.textfield(
                name=self.name, tooltip=self.label, x=0, y=0,
                width=self.fw, height=self.fh, borderStyle='inset',
                borderColor=colors.HexColor('#c8dcc8'),
                fillColor=colors.HexColor('#f8fbf9'), textColor=colors.black,
                forceBorder=True, relative=True, fieldFlags='multiline',
                maxlen=100000, fontSize=9,
            )

    def _toc(entries=None):
        items = [_p("TABLE OF CONTENTS",
                    ParagraphStyle('TTOC', parent=S['title'], fontSize=20)),
                 Spacer(1, 0.15 * inch),
                 Table([['']], colWidths=[6.5 * inch],
                       style=TableStyle([('LINEBELOW', (0, 0), (-1, -1), 1.5,
                                          colors.HexColor(VOLO_GREEN))])),
                 Spacer(1, 0.25 * inch)]
        if entries:
            name_st = ParagraphStyle('TN', parent=S['body'], fontSize=11, leading=18)
            pg_st = ParagraphStyle('TP', parent=S['body'], fontSize=11, leading=18,
                                   alignment=TA_CENTER, fontName='Helvetica-Bold',
                                   textColor=colors.HexColor(VOLO_GREEN))
            rows = [[Paragraph(label, name_st), Paragraph(str(entries[key]), pg_st)]
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
        """Recursively collect source/sources/link strings (for Citations).
        Related Research is handled separately and must NOT be passed here."""
        if isinstance(obj, dict):
            for k in ('sources', 'source', 'link'):
                v = obj.get(k)
                if isinstance(v, list):
                    for s in v:
                        if isinstance(s, str) and s.strip():
                            acc.append(s.strip())
                elif isinstance(v, str) and v.strip():
                    acc.append(v.strip())
            for vk, vv in obj.items():
                if vk in ('related_research',):
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

        # ── Title + Innovation Summary
        story.append(_p("TECHNICAL DUE DILIGENCE REPORT", S["title"]))
        story.append(_p(f"<b>{name}</b>", S["heading"]))
        meta = f"Report Date: {datetime.now().strftime('%B %d, %Y')}"
        if field:
            meta = f"Field: {field} &nbsp;|&nbsp; " + meta
        story.append(_p(meta, S["body"]))
        story.append(Spacer(1, 0.12 * inch))

        if analysis.get('partial'):
            story.append(_p(
                f"<b><font color='{WARN_ORANGE}'>&#9888; PARTIAL REPORT</font></b> &mdash; the final "
                f"synthesis did not complete ({analysis.get('partial_reason', 'unknown reason')}). "
                f"The sections below were assembled from the document reads and research gathered "
                f"before the run ended; re-run to produce the fully synthesized report.",
                S["small"]))
            story.append(Spacer(1, 0.1 * inch))

        summ = analysis.get('innovation_summary', {}) or {}
        story.append(_Anchor("summary"))
        story.append(_p("INNOVATION SUMMARY", S["heading"]))
        if summ.get('one_liner'):
            story.append(_p(f"<b>{summ['one_liner']}</b>", S["body"]))
        if summ.get('inferred_innovation'):
            story.append(_p(summ['inferred_innovation'], S["body"]))

        hyp = (summ.get('analyst_hypothesis') or '').strip()
        if hyp and hyp.lower() not in ('none', 'none provided', '(none provided)'):
            story.append(Spacer(1, 0.06 * inch))
            story.append(_p("Analyst's Hypothesis (tested against the evidence):", S["sub"]))
            story.append(_p(f"<i>{hyp}</i>", S["small"]))
            assess = summ.get('hypothesis_assessment', 'UNVERIFIABLE')
            story.append(_p(
                f"<b>Assessment: <font color='{_sev_color(assess)}'>{assess}</font></b> — "
                f"{summ.get('hypothesis_explanation', '')}",
                S["small"],
            ))
        story.append(Spacer(1, 0.12 * inch))
        story.append(_Commentary("c_summary", "Team Commentary — Innovation Summary:"))
        story.append(PageBreak())

        # ── TOC
        story.extend(_toc(toc_entries))

        # ── Technology Explainer
        tech = analysis.get('technology_explainer', {}) or {}
        story.append(_Anchor("explainer"))
        story.append(_p("TECHNOLOGY EXPLAINER", S["heading"]))
        story.append(_p("<i>Written for an engineer or scientist who is not a specialist in this domain.</i>", S["small"]))
        if tech.get('plain_language'):
            story.append(_p("In Plain Language", S["sub"]))
            story.append(_p(tech['plain_language'], S["body"]))
        if tech.get('technical_depth'):
            story.append(_p("Technical Depth", S["sub"]))
            story.append(_p(tech['technical_depth'], S["body"]))
        terms = tech.get('key_terms', []) or []
        if terms:
            story.append(_p("Key Terms", S["sub"]))
            for t in terms:
                story.append(_p(f"<b>{t.get('term', '')}</b> — {t.get('definition', '')}", S["small"]))
        story.append(Spacer(1, 0.1 * inch))
        story.append(_Commentary("c_explainer", "Team Commentary — Technology Explainer:"))
        story.append(PageBreak())

        # ── How It Works
        how = analysis.get('how_it_works', {}) or {}
        if how.get('summary') or how.get('steps'):
            story.append(_Anchor("howitworks"))
            story.append(_p("HOW IT WORKS", S["heading"]))
            if how.get('summary'):
                story.append(_p(how['summary'], S["body"]))
            for i, step in enumerate(how.get('steps', []) or [], 1):
                story.append(_p(f"<b>{i}.</b> {step}", S["small"]))
            story.append(Spacer(1, 0.1 * inch))

        # ── Novelty vs Prior Work
        nov = analysis.get('novelty_vs_prior_work', {}) or {}
        story.append(_Anchor("novelty"))
        story.append(_p("NOVELTY VS. PRIOR WORK", S["heading"]))
        if nov.get('summary'):
            story.append(_p(nov['summary'], S["body"]))
        new = nov.get('whats_genuinely_new', []) or []
        if new:
            story.append(_p("What Appears Genuinely New", S["sub"]))
            for x in new:
                story.append(_p(f"- {x}", S["small"]))
        comps = nov.get('comparisons', []) or []
        if comps:
            story.append(_p("Compared to Prior Approaches", S["sub"]))
            for c in comps:
                src = c.get('source', '')
                src_txt = f" <i>[{_link(src, src)}]</i>" if src else ""
                adv = f" <b>Edge:</b> {c['advantage']}" if c.get('advantage') else ""
                story.append(_p(
                    f"<b>{c.get('prior_approach', 'Prior approach')}:</b> "
                    f"{c.get('how_this_differs', '')}.{adv}{src_txt}", S["small"]))
        story.append(Spacer(1, 0.1 * inch))
        story.append(_Commentary("c_novelty", "Team Commentary — Novelty:"))
        story.append(PageBreak())

        # ── Evidence & Data
        ev = analysis.get('evidence_and_data', {}) or {}
        story.append(_Anchor("evidence"))
        story.append(_p("EVIDENCE & DATA SOURCES", S["heading"]))
        for r in ev.get('key_results', []) or []:
            strength = r.get('strength', '')
            badge = (f" <font color='{_sev_color(strength)}'>[{strength}]</font>"
                     if strength else "")
            src = r.get('source', '')
            src_txt = f" <i>[{_link(src, src)}]</i>" if src else ""
            story.append(_p(
                f"<b>{r.get('claim', '')}</b>{badge}<br/>{r.get('evidence', '')}{src_txt}",
                S["small"]))
            story.append(Spacer(1, 0.03 * inch))
        datasets = ev.get('datasets', []) or []
        if datasets:
            story.append(_p("Datasets & Benchmarks", S["sub"]))
            for d in datasets:
                link = d.get('link', '')
                title = _link(d.get('name', 'Dataset'), link)
                story.append(_p(f"<b>{title}</b> — {d.get('description', '')}", S["small"]))
        oq = ev.get('open_questions', []) or []
        if oq:
            story.append(_p("Open Questions / Needs Verification", S["sub"]))
            for q in oq:
                story.append(_p(f"- {q}", S["small"]))
        story.append(Spacer(1, 0.1 * inch))
        story.append(_Commentary("c_evidence", "Team Commentary — Evidence:"))
        story.append(PageBreak())

        # ── Commercial Implications
        com = analysis.get('commercial_implications', {}) or {}
        story.append(_Anchor("commercial"))
        story.append(_p("COMMERCIAL IMPLICATIONS", S["heading"]))
        if com.get('summary'):
            story.append(_p(com['summary'], S["body"]))
        apps = com.get('applications', []) or []
        if apps:
            story.append(_p("Applications", S["sub"]))
            for a in apps:
                mkt = f" <i>({a['market']})</i>" if a.get('market') else ""
                story.append(_p(f"<b>{a.get('application', '')}</b>{mkt} — {a.get('notes', '')}", S["small"]))
        if com.get('market_context'):
            story.append(_p("Market Context", S["sub"]))
            story.append(_p(com['market_context'], S["small"]))
        cc = com.get('comparable_companies', []) or []
        if cc:
            story.append(_p("Comparable Companies", S["sub"]))
            for c in cc:
                val = f" ({c['valuation_or_revenue']})" if c.get('valuation_or_revenue') else ""
                story.append(_p(f"<b>{c.get('name', '')}</b>{val}: {c.get('context', '')}", S["small"]))
        story.append(Spacer(1, 0.1 * inch))
        story.append(_Commentary("c_commercial", "Team Commentary — Commercial:"))
        story.append(PageBreak())

        # ── Manufacturing Scale-Up & Risk
        man = analysis.get('manufacturing_scaleup_risk', {}) or {}
        story.append(_Anchor("manufacturing"))
        story.append(_p("MANUFACTURING SCALE-UP & COMMERCIALIZATION RISK", S["heading"]))
        if man.get('readiness'):
            story.append(_p(f"<b>Readiness:</b> {man['readiness']}", S["small"]))
        if man.get('scale_up_path'):
            story.append(_p(man['scale_up_path'], S["body"]))
        risks = man.get('key_risks', []) or []
        if risks:
            story.append(_p("Key Risks", S["sub"]))
            for r in risks:
                sev = r.get('severity', '')
                badge = f"<font color='{_sev_color(sev)}'>[{sev}]</font> " if sev else ""
                mit = f" <i>Mitigation:</i> {r['mitigation']}" if r.get('mitigation') else ""
                story.append(_p(f"{badge}<b>{r.get('risk', '')}</b>.{mit}", S["small"]))
        story.append(Spacer(1, 0.1 * inch))
        story.append(_Commentary("c_manufacturing", "Team Commentary — Manufacturing & Risk:"))
        story.append(PageBreak())

        # ── Claims Assessment (retained rigor)
        claims = analysis.get('claims', []) or []
        if claims:
            story.append(_Anchor("claims"))
            story.append(_p("CLAIMS ASSESSMENT", S["heading"]))
            for cl in claims:
                vs = cl.get('verification_status', 'UNVERIFIED')
                typ = (cl.get('type', 'OTHER') or 'OTHER')[:11].upper()
                src = cl.get('sources', []) or []
                src_txt = f" <i>[{', '.join(src[:2])}]</i>" if src else ""
                inv = f"<br/><i>Investigate:</i> {cl['what_needs_investigation']}" if cl.get('what_needs_investigation') else ""
                story.append(_p(
                    f"<b>[{typ}] {cl.get('claim', '')}</b> "
                    f"<font color='{_sev_color(vs)}'>({vs})</font>{inv}{src_txt}", S["small"]))
                story.append(Spacer(1, 0.03 * inch))
            story.append(Spacer(1, 0.08 * inch))
            story.append(_Commentary("c_claims", "Team Commentary — Claims:"))
            story.append(PageBreak())

        # ── Competitive Landscape (retained rigor)
        comp = analysis.get('competitive_landscape', {}) or {}
        if comp:
            story.append(_Anchor("competitive"))
            story.append(_p("COMPETITIVE LANDSCAPE", S["heading"]))
            if comp.get('positioning_summary'):
                story.append(_p(comp['positioning_summary'], S["body"]))
            peers = comp.get('peer_competitors', []) or []
            if peers:
                story.append(_p("Peer-Stage Competitors", S["sub"]))
                for pr in peers:
                    src = pr.get('sources', []) or []
                    src_txt = f" <i>[{', '.join(src[:2])}]</i>" if src else ""
                    story.append(_p(
                        f"<b>{pr.get('name', '')}</b> ({pr.get('stage', '?')}): "
                        f"{pr.get('description', '')}{src_txt}", S["small"]))
            leaders = comp.get('market_leaders', []) or []
            if leaders:
                story.append(_p("Market Leaders & Incumbents", S["sub"]))
                for ld in leaders:
                    pos = f" — {ld['market_position']}" if ld.get('market_position') else ""
                    story.append(_p(f"<b>{ld.get('name', '')}</b>{pos}: {ld.get('description', '')}", S["small"]))
            if comp.get('competitive_risks'):
                story.append(_p("<b>Competitive Risks:</b> " + " &middot; ".join(comp['competitive_risks']), S["small"]))
            if comp.get('potential_acquirers'):
                story.append(_p("<b>Potential Acquirers:</b> " + " &middot; ".join(comp['potential_acquirers']), S["small"]))
            story.append(Spacer(1, 0.1 * inch))

        # ── Related Research / Further Reading (DISTINCT from Citations)
        related = analysis.get('related_research', []) or []
        story.append(_Anchor("related"))
        story.append(_p("RELATED RESEARCH / FURTHER READING", S["heading"]))
        story.append(_p(
            "<i>A curated reading list of high-quality literature on the underlying "
            "science, for diligencers who want to go deeper. This is separate from "
            "the Citations used to build this report.</i>", S["small"]))
        story.append(Spacer(1, 0.05 * inch))
        if related:
            for i, paper in enumerate(related, 1):
                title = paper.get('title', 'Untitled')
                linked = _link(title, paper.get('link', ''))
                authors = paper.get('authors', '')
                venue = paper.get('venue_year', '')
                meta_bits = " &middot; ".join([b for b in (authors, venue) if b])
                meta_line = f"<br/><font color='{MUTED}'>{meta_bits}</font>" if meta_bits else ""
                why = f"<br/><i>{paper['why_relevant']}</i>" if paper.get('why_relevant') else ""
                story.append(_p(f"<b>{i}. {linked}</b>{meta_line}{why}", S["small"]))
                story.append(Spacer(1, 0.04 * inch))
        else:
            story.append(_p("No related research was surfaced for this subject.", S["small"]))
        story.append(Spacer(1, 0.1 * inch))
        story.append(_Commentary("c_related", "Team Commentary — Further Reading Notes:"))
        story.append(PageBreak())

        # ── Conclusion
        concl = analysis.get('conclusion', {}) or {}
        story.append(_Anchor("conclusion"))
        story.append(_p("CONCLUSION", S["heading"]))
        if concl.get('summary'):
            story.append(_p(concl['summary'], S["body"]))
        prove = concl.get('what_must_be_proven', []) or []
        if prove:
            story.append(_p("What Must Be Proven Next", S["sub"]))
            for x in prove:
                story.append(_p(f"- {x}", S["small"]))
        story.append(Spacer(1, 0.12 * inch))
        story.append(_p(
            f"<i><b>Methodology:</b> Multi-pass technical analysis — full reads of the "
            f"uploaded document(s), web research for novelty/datasets/market, and "
            f"synthesis. {analysis.get('sources_consulted', '?')} external sources "
            f"consulted. No investment recommendation is made.</i><br/>"
            f"<b>Generated:</b> {datetime.now().strftime('%B %d, %Y at %H:%M:%S')}",
            S["small"]))
        story.append(Spacer(1, 0.12 * inch))
        story.append(_Commentary("c_conclusion", "Team Commentary — Final Notes & Next Steps:", h=1.8 * inch))

        # ── Citations
        story.append(PageBreak())
        story.append(_Anchor("citations"))
        story.append(_p("CITATIONS", S["heading"]))
        story.append(_p("<i>Sources used to build this report (the Related Research list above is separate).</i>", S["small"]))

        acc = []
        for key in ('claims', 'novelty_vs_prior_work', 'evidence_and_data',
                    'commercial_implications', 'manufacturing_scaleup_risk',
                    'competitive_landscape'):
            _section_sources(analysis.get(key), acc)
        acc.extend(analysis.get('_research_sources', []) or [])

        seen = set()
        unique = []
        for s in acc:
            k = s.lower()
            if k not in seen:
                seen.add(k)
                unique.append(s)
        src_st = ParagraphStyle('TSrc', parent=S['small'], fontSize=8, leading=11,
                                spaceAfter=2, textColor=colors.HexColor('#333333'))
        if unique:
            for s in unique:
                story.append(_p(f"- {_link(s, s)}", src_st))
        else:
            story.append(_p("No external citations were recorded.", src_st))

        docs = analysis.get('_doc_filenames', []) or []
        if docs:
            story.append(Spacer(1, 0.08 * inch))
            story.append(_p("<b>Documents analyzed:</b> " + ", ".join(docs), src_st))
        story.append(Spacer(1, 0.06 * inch))
        story.append(_p(f"<b>Total unique external sources:</b> {len(unique)}", src_st))

        return story

    _args = dict(pagesize=letter, topMargin=0.65 * inch, bottomMargin=0.65 * inch,
                 leftMargin=0.7 * inch, rightMargin=0.7 * inch)

    # Pass 1: capture page numbers.
    SimpleDocTemplate(io.BytesIO(), **_args).build(_story())
    captured = dict(toc_tracker)
    toc_tracker.clear()
    # Pass 2: real build with populated TOC.
    SimpleDocTemplate(output_path, **_args).build(_story(toc_entries=captured))
