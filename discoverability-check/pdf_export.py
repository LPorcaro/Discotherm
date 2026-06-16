"""One-page PDF rendering for the discoverability report.

``generate_report_pdf`` takes the same report dict the JSON endpoint returns and produces a
print-ready PDF using reportlab's Platypus high-level API. The layout is intentionally a single
page: a header (artist name + thumbnail + date), a large overall score with a coloured progress
bar, the five diagnostics (score, status badge, detail numbers, recommendation), and a footer.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    Flowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Status -> accent colour. Mirrors the thresholds in scoring._status (good/warning/critical) plus
# a neutral grey for the insufficient_data sentinel.
_STATUS_COLORS = {
    "good": colors.HexColor("#06b6d4"),       # cyan
    "warning": colors.HexColor("#f59e0b"),    # orange
    "critical": colors.HexColor("#ef4444"),   # red
    "insufficient_data": colors.HexColor("#94a3b8"),  # slate grey
}
_TEXT = colors.HexColor("#0f172a")
_MUTED = colors.HexColor("#64748b")
_TRACK_BG = colors.HexColor("#e2e8f0")


def _status_color(status: str):
    return _STATUS_COLORS.get(status, _MUTED)


def _fmt(value) -> str:
    """Format a detail value compactly: ints with thousands separators, floats trimmed."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "—"
    if value is None:
        return "—"
    return str(value)


class ScoreBar(Flowable):
    """A horizontal progress bar: a grey track with a coloured fill proportional to ``score``."""

    def __init__(self, score: float, color, width: float, height: float = 7 * mm):
        super().__init__()
        self.score = max(0.0, min(100.0, float(score)))
        self.color = color
        self.width = width
        self.height = height

    def draw(self):
        c = self.canv
        radius = self.height / 2
        c.setFillColor(_TRACK_BG)
        c.roundRect(0, 0, self.width, self.height, radius, stroke=0, fill=1)
        fill_w = self.width * (self.score / 100.0)
        if fill_w > 0:
            c.setFillColor(self.color)
            # Clamp the corner radius so a tiny fill still renders as a rounded pill.
            c.roundRect(0, 0, max(fill_w, radius * 2), self.height, radius, stroke=0, fill=1)


def _badge(text: str, color) -> Table:
    """A small pill-shaped status badge implemented as a single coloured table cell."""
    style = ParagraphStyle(
        "badge", fontName="Helvetica-Bold", fontSize=7, textColor=colors.white,
        alignment=TA_LEFT, leading=9,
    )
    label = text.upper().replace("_", " ")
    cell = Paragraph(label, style)
    text_w = stringWidth(label, "Helvetica-Bold", 7)
    t = Table([[cell]], colWidths=[text_w + 12])  # text + 5pt padding each side + 2pt buffer
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def generate_report_pdf(report: dict, image_bytes: bytes | None = None) -> bytes:
    """Render ``report`` (the JSON report payload) to PDF bytes."""
    buf = io.BytesIO()
    content_width = A4[0] - 30 * mm
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=12 * mm,
        title=f"Discotherm — {report.get('artist_name', 'Artist')}",
    )

    base = getSampleStyleSheet()
    h_artist = ParagraphStyle("artist", parent=base["Title"], fontSize=22, leading=25,
                              textColor=_TEXT, spaceAfter=0)
    h_sub = ParagraphStyle("sub", parent=base["Normal"], fontSize=9, textColor=_MUTED, leading=12)
    h_diag = ParagraphStyle("diag", parent=base["Heading2"], fontSize=11, leading=13,
                            textColor=_TEXT, spaceAfter=0)
    body = ParagraphStyle("body", parent=base["Normal"], fontSize=8.5, leading=12,
                          textColor=_TEXT, spaceBefore=2)
    detail_style = ParagraphStyle("detail", parent=base["Normal"], fontSize=8, textColor=_MUTED,
                                  leading=11)
    score_big = ParagraphStyle("scorebig", parent=base["Title"], fontSize=40, leading=42,
                               textColor=_TEXT)
    score_lbl = ParagraphStyle("scorelbl", parent=base["Normal"], fontSize=9, textColor=_MUTED,
                               leading=11, alignment=TA_RIGHT)

    story: list = []

    artist_name = report.get("artist_name", "Unknown Artist")
    generated = datetime.now(timezone.utc).strftime("%d %b %Y")

    # ---- Header: name + subtitle on the left, thumbnail on the right -------------------------
    header_left = [
        Paragraph(artist_name, h_artist),
        Spacer(1, 2),
        Paragraph("Discotherm", h_sub),
        Paragraph(f"Generated {generated}", h_sub),
    ]
    thumb = None
    if image_bytes:
        try:
            thumb = Image(io.BytesIO(image_bytes), width=22 * mm, height=22 * mm)
        except Exception:
            thumb = None
    header_row = [[header_left, thumb if thumb is not None else ""]]
    header = Table(header_row, colWidths=[content_width - 24 * mm, 24 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
        ("VALIGN", (1, 0), (1, 0), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header)
    story.append(Spacer(1, 6))
    story.append(_rule(content_width))
    story.append(Spacer(1, 8))

    # ---- Overall score block -----------------------------------------------------------------
    overall_score = report.get("overall_score", 0)
    overall_status = report.get("overall_status", "insufficient_data")
    o_color = _status_color(overall_status)
    total_tracks = report.get("total_tracks", 0)

    score_cell = [
        Paragraph(f'{overall_score}<font size=14 color="#64748b">/100</font>', score_big),
    ]
    right_cell = [
        Paragraph("OVERALL DISCOVERABILITY", score_lbl),
        Spacer(1, 3),
        _badge(overall_status, o_color),
        Spacer(1, 3),
        Paragraph(f"{_fmt(total_tracks)} tracks analysed", score_lbl),
    ]
    score_tbl = Table([[score_cell, right_cell]], colWidths=[content_width * 0.5, content_width * 0.5])
    score_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(score_tbl)
    story.append(Spacer(1, 6))
    story.append(ScoreBar(overall_score, o_color, content_width))
    story.append(Spacer(1, 4))

    # ---- Touring annotation (optional) --------------------------------------------------------
    touring = report.get("touring_context")
    if touring:
        shows = touring.get("show_count", 0)
        recent = touring.get("most_recent_show_date")
        active = touring.get("is_active_touring_artist")
        bits = [f"{_fmt(shows)} recent shows"]
        if recent:
            bits.append(f"most recent {recent}")
        bits.append("actively touring" if active else "no recent touring")
        story.append(Paragraph("🎤 Touring: " + " · ".join(bits), detail_style))
        story.append(Spacer(1, 4))

    story.append(_rule(content_width))
    story.append(Spacer(1, 8))

    # ---- Diagnostics -------------------------------------------------------------------------
    diagnostics = report.get("diagnostics", []) or []
    for diag in diagnostics:
        story.extend(_diagnostic_block(diag, content_width, h_diag, body, detail_style))

    # ---- Footer ------------------------------------------------------------------------------
    story.append(Spacer(1, 6))
    story.append(_rule(content_width))
    story.append(Spacer(1, 5))
    note = report.get("note")
    footer_style = ParagraphStyle("footer", parent=base["Italic"], fontSize=7.5, textColor=_MUTED,
                                  leading=10)
    if note:
        story.append(Paragraph(f"Note: {note}", footer_style))
        story.append(Spacer(1, 2))
    story.append(Paragraph(
        "Generated by Discotherm — built for the Musixmatch Musicathon 2026",
        footer_style,
    ))

    doc.build(story)
    return buf.getvalue()


def _rule(width: float) -> Table:
    """A thin horizontal divider line."""
    t = Table([[""]], colWidths=[width], rowHeights=[0.4 * mm])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0"))]))
    return t


def _diagnostic_block(diag: dict, width: float, title_style, body_style, detail_style) -> list:
    """Render one diagnostic: title row (name + score + badge), detail numbers, recommendation."""
    name = (diag.get("name") or "").replace("_", " ").title()
    score = diag.get("score")
    status = diag.get("status", "insufficient_data")
    color = _status_color(status)

    title_para = Paragraph(name, title_style)
    score_txt = "—" if score is None else f"{score}"
    score_para = Paragraph(
        f'<font size=12 color="#0f172a"><b>{score_txt}</b></font>'
        f'<font size=8 color="#64748b">/100</font>',
        ParagraphStyle("ds", fontName="Helvetica", fontSize=12, alignment=TA_RIGHT, leading=14),
    )
    title_tbl = Table(
        [[title_para, _badge(status, color), score_para]],
        colWidths=[width * 0.5, width * 0.28, width * 0.22],
    )
    title_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (2, 0), (2, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    flow: list = [title_tbl, Spacer(1, 2), ScoreBar(score or 0, color, width, height=3.5 * mm)]

    detail = diag.get("detail") or {}
    if isinstance(detail, dict) and detail:
        parts = [f"{k.replace('_', ' ')}: <b>{_fmt(v)}</b>" for k, v in detail.items()]
        flow.append(Spacer(1, 3))
        flow.append(Paragraph("  ·  ".join(parts), detail_style))

    rec = diag.get("recommendation")
    if rec:
        flow.append(Paragraph(rec, body_style))

    flow.append(Spacer(1, 9))
    return flow
