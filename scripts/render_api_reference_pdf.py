from __future__ import annotations

import argparse
import html
import logging
import re
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
)

if TYPE_CHECKING:
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus.doctemplate import BaseDocTemplate

DEFAULT_SOURCE = Path("docs/api-reference.md")
DEFAULT_OUTPUT = Path("docs/api-reference.pdf")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^-\s+(.*)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
logger = logging.getLogger(__name__)


def _format_text_segment(value: str) -> str:
    escaped = html.escape(value, quote=False)
    return _BOLD_RE.sub(lambda match: f"<b>{html.escape(match.group(1), quote=False)}</b>", escaped)


def format_inline(value: str) -> str:
    rendered: list[str] = []
    cursor = 0
    while cursor < len(value):
        code_start = value.find("`", cursor)
        if code_start == -1:
            rendered.append(_format_text_segment(value[cursor:]))
            break

        rendered.append(_format_text_segment(value[cursor:code_start]))
        code_end = value.find("`", code_start + 1)
        if code_end == -1:
            rendered.append(_format_text_segment(value[code_start:]))
            break

        code_value = html.escape(value[code_start + 1 : code_end], quote=False)
        rendered.append(f'<font name="Courier">{code_value}</font>')
        cursor = code_end + 1

    return "".join(rendered)


def wrap_code_block(value: str, *, width: int = 88) -> str:
    wrapped_lines: list[str] = []
    for line in value.splitlines():
        if not line:
            wrapped_lines.append("")
            continue
        parts = textwrap.wrap(
            line,
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
            drop_whitespace=False,
            replace_whitespace=False,
        )
        wrapped_lines.extend(parts or [""])
    return "\n".join(wrapped_lines)


def parse_markdown_blocks(value: str) -> list[tuple[str, object]]:
    blocks: list[tuple[str, object]] = []
    paragraph_lines: list[str] = []
    bullet_items: list[str] = []
    code_lines: list[str] = []
    in_code_block = False

    def flush_paragraph() -> None:
        if paragraph_lines:
            paragraph = " ".join(line.strip() for line in paragraph_lines)
            blocks.append(("paragraph", paragraph))
            paragraph_lines.clear()

    def flush_bullets() -> None:
        if bullet_items:
            blocks.append(("bullets", bullet_items.copy()))
            bullet_items.clear()

    for raw_line in value.splitlines():
        line = raw_line.rstrip()

        if line.startswith("```"):
            flush_paragraph()
            flush_bullets()
            if in_code_block:
                blocks.append(("code", "\n".join(code_lines)))
                code_lines.clear()
                in_code_block = False
            else:
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(raw_line.rstrip("\n"))
            continue

        if not line.strip():
            flush_paragraph()
            flush_bullets()
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match is not None:
            flush_paragraph()
            flush_bullets()
            level = len(heading_match.group(1))
            blocks.append(("heading", (level, heading_match.group(2).strip())))
            continue

        bullet_match = _BULLET_RE.match(line)
        if bullet_match is not None:
            flush_paragraph()
            bullet_items.append(bullet_match.group(1).strip())
            continue

        flush_bullets()
        paragraph_lines.append(line)

    flush_paragraph()
    flush_bullets()
    if code_lines:
        blocks.append(("code", "\n".join(code_lines)))

    return blocks


def build_styles() -> dict[str, ParagraphStyle]:
    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ApiReferenceTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=26,
            leading=30,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#102a43"),
            spaceAfter=16,
        ),
        "heading2": ParagraphStyle(
            "ApiReferenceHeading2",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=17,
            leading=22,
            textColor=colors.HexColor("#102a43"),
            spaceBefore=10,
            spaceAfter=8,
        ),
        "heading3": ParagraphStyle(
            "ApiReferenceHeading3",
            parent=styles["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=16,
            textColor=colors.HexColor("#243b53"),
            spaceBefore=8,
            spaceAfter=6,
        ),
        "paragraph": ParagraphStyle(
            "ApiReferenceBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=14,
            textColor=colors.HexColor("#102a43"),
            spaceAfter=8,
        ),
        "bullet": ParagraphStyle(
            "ApiReferenceBullet",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=14,
            textColor=colors.HexColor("#102a43"),
            leftIndent=12,
        ),
        "code": ParagraphStyle(
            "ApiReferenceCode",
            parent=styles["Code"],
            fontName="Courier",
            fontSize=8.6,
            leading=10.6,
            leftIndent=8,
            rightIndent=8,
            borderPadding=8,
            borderColor=colors.HexColor("#d9e2ec"),
            borderWidth=0.5,
            borderRadius=4,
            backColor=colors.HexColor("#f7fafc"),
            textColor=colors.HexColor("#102a43"),
            spaceAfter=10,
        ),
        "note": ParagraphStyle(
            "ApiReferenceNote",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#486581"),
            spaceAfter=12,
        ),
    }


def draw_page_footer(canvas: Canvas, doc: BaseDocTemplate) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(colors.HexColor("#7b8794"))
    canvas.drawString(doc.leftMargin, 9 * mm, "Agent Marketplace API Reference")
    canvas.drawRightString(A4[0] - doc.rightMargin, 9 * mm, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def render_markdown_to_pdf(source: Path, output: Path) -> None:
    styles = build_styles()
    blocks = parse_markdown_blocks(source.read_text())
    story = [Paragraph("Agent Marketplace Backend", styles["note"])]

    for kind, payload in blocks:
        if kind == "heading":
            level, text = payload  # type: ignore[misc]
            if level == 1:
                story.append(Paragraph(format_inline(str(text)), styles["title"]))
                story.append(
                    Paragraph(
                        f"Generated from <font name='Courier'>{html.escape(str(source))}</font>",
                        styles["note"],
                    )
                )
            elif level == 2:
                story.append(Paragraph(format_inline(str(text)), styles["heading2"]))
            else:
                story.append(Paragraph(format_inline(str(text)), styles["heading3"]))
            continue

        if kind == "paragraph":
            story.append(Paragraph(format_inline(str(payload)), styles["paragraph"]))
            continue

        if kind == "bullets":
            items = [
                ListItem(Paragraph(format_inline(item), styles["bullet"]))
                for item in payload  # type: ignore[union-attr]
            ]
            story.append(
                ListFlowable(
                    items,
                    bulletType="bullet",
                    start="circle",
                    leftIndent=12,
                )
            )
            story.append(Spacer(1, 6))
            continue

        if kind == "code":
            story.append(Preformatted(wrap_code_block(str(payload)), styles["code"]))
            continue

    output.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Agent Marketplace API Reference",
        author="Agent Marketplace Backend",
        subject="API reference",
    )
    document.build(story, onFirstPage=draw_page_footer, onLaterPages=draw_page_footer)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the API reference Markdown to PDF.")
    parser.add_argument("source", nargs="?", default=str(DEFAULT_SOURCE))
    parser.add_argument("output", nargs="?", default=str(DEFAULT_OUTPUT))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = Path(args.source)
    output = Path(args.output)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    render_markdown_to_pdf(source, output)
    logger.info("Wrote %s", output)


if __name__ == "__main__":
    main()
