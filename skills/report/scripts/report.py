#!/usr/bin/env python3
"""Report authoring helper: draft outline -> build DOCX -> inspect/validate.

Standard library only -- `python3 scripts/report.py --help` works with
zero setup. The .docx is produced with zipfile + XML only, no external
engines. All source text is escaped so it can never break the OOXML.

Adapted (ideas only) from JetBrains/skills doc/ (Apache-2.0). See the
LICENSE file and SKILL.md for attribution.
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC = "http://purl.org/dc/elements/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
A4 = (11906, 16838)          # A4 in twips
MARGIN = 1440               # 1 inch
TITLE_DEFAULT = "Untitled report"
DEFAULT_SECTIONS = ["Summary", "Background", "Findings", "Recommendations",
                    "Appendix"]


class ReportError(Exception):
    """User-facing failure: printed as `Error: <message>`, exit 1."""


def esc(text: str) -> str:
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


def esc_attr(text: str) -> str:
    return esc(text).replace('"', "&quot;")


# --------------------------------------------------------------------------
# inline markdown


INLINE_RE = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<link>\[(?P<link_text>[^\]]*)\]\((?P<link_url>[^()\s]+)\))"
    r"|(?P<bold>\*\*(?P<bold_text>.+?)\*\*)"
    r"|(?P<italic>(?<!\*)\*(?P<italic_text>[^*]+?)\*(?!\*))"
)


def inline_runs(text: str) -> list[tuple]:
    """Inline markdown -> runs.

    Each run is (kind, value): 'text'/'b'/'i'/'code' with the text, or
    'link' with (label, url).
    """
    runs: list[tuple] = []
    pos = 0
    for match in INLINE_RE.finditer(text):
        if match.start() > pos:
            runs.append(("text", text[pos:match.start()]))
        if match.group("code") is not None:
            runs.append(("code", match.group("code")[1:-1]))
        elif match.group("link") is not None:
            runs.append(("link", (match.group("link_text"),
                                  match.group("link_url"))))
        elif match.group("bold") is not None:
            runs.append(("b", match.group("bold_text")))
        elif match.group("italic") is not None:
            runs.append(("i", match.group("italic_text")))
        pos = match.end()
    if pos < len(text):
        runs.append(("text", text[pos:]))
    return [(kind, value) for kind, value in runs
            if not (kind == "text" and value == "")]


# --------------------------------------------------------------------------
# block parser


FENCE_RE = re.compile(r"^```(\S*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
ORDERED_RE = re.compile(r"^\d+[.)]\s+(.*)$")
HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
DELIM_RE = re.compile(r"^\|?[\s:|-]+\|?$")


def split_pipe(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def parse_blocks(source: str) -> list[tuple]:
    """Markdown -> ordered list of block tuples.

    ('h', level, text), ('p', text), ('ul', [text...]), ('ol', [text...]),
    ('code', (lang, text)), ('quote', text), ('hr',), ('table', [rows]).
    """
    blocks: list[tuple] = []
    lines = source.splitlines()
    index = 0
    total = len(lines)
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(("p", " ".join(paragraph).strip()))
            paragraph = []

    while index < total:
        line = lines[index]
        stripped = line.strip()

        if in_code:
            if stripped.startswith("```"):
                blocks.append(("code", (code_lang, "\n".join(code_lines))))
                in_code = False
                code_lines = []
            else:
                code_lines.append(line)
            index += 1
            continue

        fence = FENCE_RE.match(stripped)
        if fence:
            flush_paragraph()
            in_code = True
            code_lang = fence.group(1)
            code_lines = []
            index += 1
            continue

        if not stripped:
            flush_paragraph()
            index += 1
            continue

        if HR_RE.match(stripped):
            flush_paragraph()
            blocks.append(("hr",))
            index += 1
            continue

        heading = HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            blocks.append(("h", len(heading.group(1)),
                           heading.group(2).strip()))
            index += 1
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            quote_lines: list[str] = []
            while index < total and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip()[1:].strip())
                index += 1
            blocks.append(("quote", " ".join(quote_lines)))
            continue

        if BULLET_RE.match(stripped):
            flush_paragraph()
            items: list[str] = []
            while index < total and BULLET_RE.match(lines[index].strip()):
                items.append(BULLET_RE.match(lines[index].strip()).group(1))
                index += 1
            blocks.append(("ul", items))
            continue

        if ORDERED_RE.match(stripped):
            flush_paragraph()
            items = []
            while index < total and ORDERED_RE.match(lines[index].strip()):
                items.append(ORDERED_RE.match(lines[index].strip()).group(1))
                index += 1
            blocks.append(("ol", items))
            continue

        # GFM pipe table: header row followed by a delimiter row.
        if "|" in stripped and index + 1 < total:
            delimiter = lines[index + 1].strip()
            if "|" in delimiter and DELIM_RE.match(delimiter) and "-" in delimiter:
                flush_paragraph()
                rows = [split_pipe(stripped)]
                index += 2
                while index < total and "|" in lines[index].strip():
                    rows.append(split_pipe(lines[index].strip()))
                    index += 1
                blocks.append(("table", rows))
                continue

        paragraph.append(stripped)
        index += 1

    if in_code:
        raise ReportError("unterminated code fence in source")
    flush_paragraph()
    return blocks


# --------------------------------------------------------------------------
# OOXML pieces


def run_xml(text: str, bold: bool = False, italic: bool = False,
            code: bool = False) -> str:
    props: list[str] = []
    if code:
        props.append('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" '
                     'w:cs="Consolas"/>')
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    pr = ("<w:rPr>" + "".join(props) + "</w:rPr>") if props else ""
    return f'<w:r>{pr}<w:t xml:space="preserve">{esc(text)}</w:t></w:r>'


def runs_xml(runs: list[tuple], hyperlinks: list[dict]) -> str:
    """Runs as XML; external links get a relationship id."""
    out: list[str] = []
    for kind, value in runs:
        if kind == "text":
            out.append(run_xml(value))
        elif kind == "b":
            out.append(run_xml(value, bold=True))
        elif kind == "i":
            out.append(run_xml(value, italic=True))
        elif kind == "code":
            out.append(run_xml(value, code=True))
        elif kind == "link":
            label, url = value
            rid = f"rId{1000 + len(hyperlinks)}"
            hyperlinks.append({"rid": rid, "url": url})
            out.append(f'<w:hyperlink r:id="{rid}">'
                       f'<w:r><w:rPr><w:rStyle w:val="Hyperlink"/></w:rPr>'
                       f'<w:t xml:space="preserve">{esc(label)}</w:t></w:r>'
                       f'</w:hyperlink>')
    return "".join(out)


def table_xml(rows: list[list[str]]) -> str:
    """A bordered table; short rows are padded so every row matches."""
    width = max((len(row) for row in rows), default=1)
    padded = [row + [""] * (width - len(row)) for row in rows]
    borders = ("<w:tblBorders>"
               '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               "</w:tblBorders>")
    pieces = [
        "<w:tbl>",
        '<w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" '
        f'w:type="auto"/>{borders}</w:tblPr>',
        "<w:tblGrid>" + "".join('<w:gridCol w:w="0"/>' for _ in range(width))
        + "</w:tblGrid>",
    ]
    for row_index, row in enumerate(padded):
        header = ("<w:trPr><w:tblHeader/></w:trPr>"
                  if row_index == 0 and len(padded) > 1 else "")
        pieces.append("<w:tr>" + header)
        for cell in row:
            pieces.append(
                '<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>'
                f'<w:p>{run_xml(cell)}</w:p></w:tc>')
        pieces.append("</w:tr>")
    pieces.append("</w:tbl>")
    return "".join(pieces)


def hr_xml() -> str:
    return ('<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" '
            'w:space="1" w:color="auto"/></w:pBdr></w:pPr></w:p>')


def styles_xml() -> str:
    borders = ("<w:tblBorders>"
               '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               "</w:tblBorders>")

    def heading(level: int, size: int) -> str:
        return (f'<w:style w:type="paragraph" w:styleId="Heading{level}">'
                f'<w:name w:val="heading {level}"/><w:basedOn w:val="Normal"/>'
                f'<w:next w:val="Normal"/><w:qFormat/>'
                f'<w:pPr><w:keepNext/><w:outlineLvl w:val="{level - 1}"/>'
                f'<w:spacing w:before="240" w:after="120"/></w:pPr>'
                f'<w:rPr><w:b/><w:sz w:val="{size}"/></w:rPr></w:style>')

    return ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            f'<w:styles xmlns:w="{W}">'
            '<w:docDefaults><w:rPrDefault><w:rPr>'
            '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
            '<w:sz w:val="22"/></w:rPr></w:rPrDefault>'
            '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" '
            'w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
            '<w:name w:val="Normal"/><w:qFormat/></w:style>'
            '<w:style w:type="paragraph" w:styleId="Title">'
            '<w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/>'
            '<w:pPr><w:spacing w:after="240"/></w:pPr>'
            '<w:rPr><w:b/><w:sz w:val="56"/></w:rPr></w:style>'
            + heading(1, 32) + heading(2, 28) + heading(3, 24)
            + heading(4, 22) + heading(5, 20) + heading(6, 18)
            + '<w:style w:type="paragraph" w:styleId="Code">'
            '<w:name w:val="Code"/><w:basedOn w:val="Normal"/>'
            '<w:pPr><w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/>'
            '<w:spacing w:after="0"/></w:pPr>'
            '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
            '<w:sz w:val="18"/></w:rPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Quote">'
            '<w:name w:val="Quote"/><w:basedOn w:val="Normal"/>'
            '<w:pPr><w:pBdr><w:left w:val="single" w:sz="18" '
            'w:space="10" w:color="AAAAAA"/></w:pBdr>'
            '<w:ind w:left="720"/></w:pPr>'
            '<w:rPr><w:i/></w:rPr></w:style>'
            '<w:style w:type="table" w:styleId="TableGrid">'
            f'<w:name w:val="Table Grid"/><w:tblPr>{borders}</w:tblPr></w:style>'
            '<w:style w:type="character" w:styleId="Hyperlink">'
            '<w:name w:val="Hyperlink"/><w:rPr>'
            '<w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>'
            "</w:styles>")


def numbering_xml() -> str:
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            f'<w:numbering xmlns:w="{W}">'
            '<w:abstractNum w:abstractNumId="0">'
            '<w:multiLevelType w:val="hybridMultilevel"/>'
            '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
            '<w:lvlText w:val="&#x2022;"/><w:lvlJc w:val="left"/>'
            '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr>'
            '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" '
            'w:hint="default"/></w:rPr></w:lvl></w:abstractNum>'
            '<w:abstractNum w:abstractNumId="1">'
            '<w:multiLevelType w:val="hybridMultilevel"/>'
            '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
            '<w:lvlText w:val="%1."/><w:lvlJc w:val="left"/>'
            '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl>'
            '</w:abstractNum>'
            '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
            '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
            "</w:numbering>")


# --------------------------------------------------------------------------
# rendering


def render_docx(blocks: list[tuple], out_path: Path, title: str,
                author: str) -> tuple[int, int]:
    """Write the .docx package. Returns (paragraph count, table count)."""
    hyperlinks: list[dict] = []
    body: list[str] = []
    paragraphs = 0
    tables = 0

    for block in blocks:
        kind = block[0]
        if kind == "h":
            _, level, text = block
            style = f"Heading{min(level, 6)}"
            body.append(f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
                        f"{runs_xml(inline_runs(text), hyperlinks)}</w:p>")
            paragraphs += 1
        elif kind == "p":
            body.append(f"<w:p>{runs_xml(inline_runs(block[1]), hyperlinks)}</w:p>")
            paragraphs += 1
        elif kind in ("ul", "ol"):
            num_id = 1 if kind == "ul" else 2
            for item in block[1]:
                body.append(
                    f'<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/>'
                    f'<w:numId w:val="{num_id}"/></w:numPr></w:pPr>'
                    f"{runs_xml(inline_runs(item), hyperlinks)}</w:p>")
                paragraphs += 1
        elif kind == "code":
            _, (lang, text) = block
            for line in (text.split("\n") or [""]):
                body.append('<w:p><w:pPr><w:pStyle w:val="Code"/></w:pPr>'
                            f"{run_xml(line, code=True)}</w:p>")
                paragraphs += 1
        elif kind == "quote":
            body.append('<w:p><w:pPr><w:pStyle w:val="Quote"/></w:pPr>'
                        f"{runs_xml(inline_runs(block[1]), hyperlinks)}</w:p>")
            paragraphs += 1
        elif kind == "hr":
            body.append(hr_xml())
            paragraphs += 1
        elif kind == "table":
            body.append(table_xml(block[1]))
            body.append("<w:p/>")
            tables += 1
            paragraphs += 1

    body.append(f'<w:sectPr><w:pgSz w:w="{A4[0]}" w:h="{A4[1]}"/>'
                f'<w:pgMar w:top="{MARGIN}" w:right="{MARGIN}" '
                f'w:bottom="{MARGIN}" w:left="{MARGIN}" w:header="708" '
                f'w:footer="708" w:gutter="0"/></w:sectPr>')

    document_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    f'<w:document xmlns:w="{W}" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument'
                    '/2006/relationships"><w:body>'
                    + "".join(body) + "</w:body></w:document>")

    doc_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                f'<Relationships xmlns="{REL}">',
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats'
                '.org/officeDocument/2006/relationships/styles" '
                'Target="styles.xml"/>',
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats'
                '.org/officeDocument/2006/relationships/numbering" '
                'Target="numbering.xml"/>']
    for link in hyperlinks:
        doc_rels.append(f'<Relationship Id="{link["rid"]}" '
                        'Type="http://schemas.openxmlformats.org/officeDocument'
                        '/2006/relationships/hyperlink" '
                        f'Target="{esc_attr(link["url"])}" TargetMode="External"/>')
    doc_rels.append("</Relationships>")

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats'
        '-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd'
        '.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd'
        '.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/numbering.xml" ContentType="application/vnd'
        '.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd'
        '.openxmlformats-package.core-properties+xml"/>'
        "</Types>")

    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 f'<Relationships xmlns="{REL}">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats'
                 '.org/officeDocument/2006/relationships/officeDocument" '
                 'Target="word/document.xml"/>'
                 '<Relationship Id="rId2" Type="http://schemas.openxmlformats'
                 '.org/package/2006/relationships/metadata/core-properties" '
                 'Target="docProps/core.xml"/>'
                 "</Relationships>")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<cp:coreProperties xmlns:cp="{CP}" xmlns:dc="{DC}" '
                f'xmlns:dcterms="{DCTERMS}" xmlns:xsi="http://www.w3.org/2001/'
                'XMLSchema-instance">'
                f'<dc:title>{esc(title)}</dc:title>'
                f'<dc:creator>{esc(author or "agent-kit")}</dc:creator>'
                '<cp:lastModifiedBy>agent-kit</cp:lastModifiedBy>'
                f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
                f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified>'
                "</cp:coreProperties>")

    out_path = Path(out_path)
    if out_path.parent and str(out_path.parent):
        out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/styles.xml", styles_xml())
        zf.writestr("word/numbering.xml", numbering_xml())
        zf.writestr("word/_rels/document.xml.rels", "\n".join(doc_rels))
        zf.writestr("docProps/core.xml", core_xml)
    return paragraphs, tables


# --------------------------------------------------------------------------
# reading back


def read_body(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as zf:
        doc = ET.fromstring(zf.read("word/document.xml"))
    body = doc.find(f"{{{W}}}body")
    if body is None:
        raise ReportError("word/document.xml has no w:body")
    return body


def extract_text(node) -> str:
    return "".join(t.text or "" for t in node.iter(f"{{{W}}}t"))


# --------------------------------------------------------------------------
# commands


def cmd_outline(args: argparse.Namespace) -> None:
    title = args.title or TITLE_DEFAULT
    sections = DEFAULT_SECTIONS + list(args.section or [])
    lines = [f"# {title}", ""]
    for name in sections:
        lines += [f"## {name}", "", ""]
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out} ({len(sections)} sections)")


def cmd_build(args: argparse.Namespace) -> None:
    try:
        source = Path(args.source).read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportError(f"cannot read {args.source}: {exc.strerror}") from None
    blocks = parse_blocks(source)
    paragraphs, tables = render_docx(blocks, Path(args.output),
                                     args.title or TITLE_DEFAULT,
                                     args.author or "")
    print(f"Wrote {args.output} ({paragraphs} paragraphs, {tables} tables)")


def cmd_inspect(args: argparse.Namespace) -> None:
    body = read_body(Path(args.file))
    headings: list[str] = []
    paragraphs = 0
    tables = 0
    words = 0
    for child in body:
        if child.tag == f"{{{W}}}p":
            paragraphs += 1
            pstyle = child.find(f"{{{W}}}pPr/{{{W}}}pStyle")
            style = pstyle.get(f"{{{W}}}val") if pstyle is not None else None
            text = extract_text(child)
            words += len(text.split())
            if style in ("Title", "Heading1", "Heading2", "Heading3",
                         "Heading4", "Heading5", "Heading6"):
                level = "Title" if style == "Title" else style.replace("Heading", "")
                headings.append(f"H{level}: {text}")
        elif child.tag == f"{{{W}}}tbl":
            tables += 1
            for row in child.findall(f"{{{W}}}tr"):
                for cell in row.findall(f"{{{W}}}tc"):
                    words += len(extract_text(cell).split())
    print(f"Headings: {len(headings)}, paragraphs: {paragraphs}, "
          f"tables: {tables}")
    for heading in headings:
        print("  " + heading)
    print(f"Words: {words}")


def cmd_to_text(args: argparse.Namespace) -> None:
    body = read_body(Path(args.file))
    lines: list[str] = []
    for child in body:
        if child.tag == f"{{{W}}}p":
            lines.append(extract_text(child))
        elif child.tag == f"{{{W}}}tbl":
            for row in child.findall(f"{{{W}}}tr"):
                cells = [extract_text(cell)
                         for cell in row.findall(f"{{{W}}}tc")]
                lines.append(" | ".join(cells))
            lines.append("")
    print("\n".join(lines).rstrip())


def cmd_validate(args: argparse.Namespace) -> None:
    path = Path(args.file)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, FileNotFoundError, OSError) as exc:
        raise ReportError(f"not a valid zip: {exc}") from None
    try:
        bad = zf.testzip()
        if bad is not None:
            raise ReportError(f"corrupt entry: {bad}")
        required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml",
                    "word/styles.xml", "docProps/core.xml"}
        missing = required - set(zf.namelist())
        if missing:
            raise ReportError(f"missing parts: {', '.join(sorted(missing))}")
        for name in ("word/document.xml", "word/styles.xml"):
            ET.fromstring(zf.read(name))
    except (ET.ParseError, KeyError, zipfile.BadZipFile) as exc:
        raise ReportError(f"invalid package: {exc}") from None
    finally:
        zf.close()
    print(f"Valid: {path}")


# --------------------------------------------------------------------------
# CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="report.py",
        description="Generate and inspect reports in DOCX format.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="render a Markdown source to .docx")
    p.add_argument("source", help="Markdown source file")
    p.add_argument("-o", "--output", required=True, help="output .docx path")
    p.add_argument("--title", help="document title")
    p.add_argument("--author", help="document author")

    p = sub.add_parser("outline", help="write a report skeleton as Markdown")
    p.add_argument("-o", "--output", required=True, help="output .md path")
    p.add_argument("--title", help="report title")
    p.add_argument("--section", action="append",
                   help="extra H2 section (repeatable)")

    p = sub.add_parser("inspect", help="heading/paragraph/table summary")
    p.add_argument("file", help=".docx path")

    p = sub.add_parser("to-text", help="plain text to stdout")
    p.add_argument("file", help=".docx path")

    p = sub.add_parser("validate", help="check zip + XML integrity")
    p.add_argument("file", help=".docx path")

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"build": cmd_build, "outline": cmd_outline,
                "inspect": cmd_inspect, "to-text": cmd_to_text,
                "validate": cmd_validate}
    try:
        handlers[args.command](args)
    except ReportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ET.ParseError, zipfile.BadZipFile) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))