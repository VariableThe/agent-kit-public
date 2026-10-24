"""Tests for skills/report/scripts/report.py.

The Markdown fixture is generated programmatically, the script runs as a
real subprocess, and the .docx is inspected with zipfile + ElementTree
(the same libraries the script uses).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "skills" / "report" / "scripts" / "report.py"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

REQUIRED_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
    "word/styles.xml",
    "word/numbering.xml",
    "word/_rels/document.xml.rels",
    "docProps/core.xml",
}


def markdown_fixture() -> str:
    """A generated document exercising every supported construct."""
    return "\n".join([
        "# Quarterly Review",
        "",
        "A paragraph with **bold**, *italic*, `code` and a "
        "[link](https://example.com/report).",
        "",
        "Special characters: <angle> & ampersand \"quote\" 'apostrophe'.",
        "",
        "## Findings",
        "",
        "- first bullet",
        "- second bullet with **strong** text",
        "",
        "1. numbered one",
        "2. numbered two",
        "",
        "```python",
        "def demo():",
        "    return 42",
        "```",
        "",
        "> A quoted remark.",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        "| Uptime | 99.9% |",
        "| Errors | 3 |",
        "",
        "### Detail",
        "",
        "Closing paragraph.",
        "",
    ])


class ReportCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name, "draft.md")
        self.output = Path(self.tmp.name, "report.docx")
        self.source.write_text(markdown_fixture(), encoding="utf-8")

    def run_script(self, *cli: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *cli],
                              capture_output=True, text=True, timeout=60)

    def build(self) -> subprocess.CompletedProcess:
        return self.run_script("build", str(self.source), "-o", str(self.output))


class BuildTest(ReportCase):
    def test_build_succeeds(self) -> None:
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Wrote {} (".format(self.output), result.stdout)
        self.assertTrue(self.output.exists())

    def test_package_has_required_parts(self) -> None:
        self.assertEqual(self.build().returncode, 0)
        with zipfile.ZipFile(self.output) as zf:
            names = set(zf.namelist())
            self.assertTrue(REQUIRED_PARTS.issubset(names), names)
            self.assertIsNone(zf.testzip())

    def test_document_xml_parses(self) -> None:
        self.build()
        with zipfile.ZipFile(self.output) as zf:
            doc = ET.fromstring(zf.read("word/document.xml"))
        self.assertTrue(doc.tag.endswith("document"))
        body = doc.find(f"{{{W}}}body")
        self.assertIsNotNone(body)
        self.assertIsNotNone(body.find(f"{{{W}}}sectPr"))

    def test_heading_styles_are_real(self) -> None:
        self.build()
        with zipfile.ZipFile(self.output) as zf:
            doc = ET.fromstring(zf.read("word/document.xml"))
            styles = ET.fromstring(zf.read("word/styles.xml"))
        used = {node.get(f"{{{W}}}val")
                for node in doc.iter(f"{{{W}}}pStyle")}
        self.assertTrue({"Heading1", "Heading2", "Heading3"}.issubset(used),
                        used)
        defined = {node.get(f"{{{W}}}styleId")
                   for node in styles.iter(f"{{{W}}}style")}
        for needed in ("Title", "Heading1", "Heading6", "Code", "Quote",
                       "TableGrid"):
            self.assertIn(needed, defined)

    def test_table_structure(self) -> None:
        self.build()
        with zipfile.ZipFile(self.output) as zf:
            doc = ET.fromstring(zf.read("word/document.xml"))
        tables = list(doc.iter(f"{{{W}}}tbl"))
        self.assertEqual(len(tables), 1)
        rows = tables[0].findall(f"{{{W}}}tr")
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(len(row.findall(f"{{{W}}}tc")), 2)

    def test_special_characters_survive(self) -> None:
        self.build()
        with zipfile.ZipFile(self.output) as zf:
            doc = ET.fromstring(zf.read("word/document.xml"))
        text = "".join(node.text or "" for node in doc.iter(f"{{{W}}}t"))
        self.assertIn("<angle> & ampersand", text)
        self.assertIn("bold", text)
        self.assertIn("def demo():", text)

    def test_bold_and_italic_runs(self) -> None:
        self.build()
        with zipfile.ZipFile(self.output) as zf:
            doc = ET.fromstring(zf.read("word/document.xml"))
        bold = [r for r in doc.iter(f"{{{W}}}r")
                if r.find(f"{{{W}}}rPr/{{{W}}}b") is not None]
        italic = [r for r in doc.iter(f"{{{W}}}r")
                  if r.find(f"{{{W}}}rPr/{{{W}}}i") is not None]
        self.assertTrue(bold)
        self.assertTrue(italic)

    def test_hyperlink_relationship(self) -> None:
        self.build()
        with zipfile.ZipFile(self.output) as zf:
            doc = ET.fromstring(zf.read("word/document.xml"))
            rels = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
        links = list(doc.iter(f"{{{W}}}hyperlink"))
        self.assertEqual(len(links), 1)
        rid = links[0].get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/"
            "relationships}id")
        targets = {r.get("Id"): r.get("Target") for r in rels}
        self.assertEqual(targets.get(rid), "https://example.com/report")

    def test_unterminated_fence_fails(self) -> None:
        self.source.write_text("# Title\n\n```python\nprint(1)\n",
                               encoding="utf-8")
        result = self.build()
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("Error:"), result.stderr)

    def test_missing_source_fails(self) -> None:
        result = self.run_script("build", str(Path(self.tmp.name, "nope.md")),
                                 "-o", str(self.output))
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("Error:"), result.stderr)


class InspectTest(ReportCase):
    def test_inspect_counts(self) -> None:
        self.build()
        result = self.run_script("inspect", str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Headings: 3, paragraphs:", result.stdout)
        self.assertIn("tables: 1", result.stdout)
        self.assertIn("H1: Quarterly Review", result.stdout)
        self.assertIn("H2: Findings", result.stdout)
        self.assertIn("Words:", result.stdout)

    def test_to_text(self) -> None:
        self.build()
        result = self.run_script("to-text", str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Quarterly Review", result.stdout)
        self.assertIn("Uptime | 99.9%", result.stdout)

    def test_validate_accepts_good_file(self) -> None:
        self.build()
        result = self.run_script("validate", str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Valid:", result.stdout)

    def test_validate_rejects_corrupt_file(self) -> None:
        broken = Path(self.tmp.name, "broken.docx")
        broken.write_bytes(b"not a zip file at all")
        result = self.run_script("validate", str(broken))
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("Error:"), result.stderr)

    def test_validate_rejects_truncated_package(self) -> None:
        self.build()
        truncated = Path(self.tmp.name, "truncated.docx")
        with zipfile.ZipFile(self.output) as zf:
            names = zf.namelist()
            payload = {name: zf.read(name) for name in names
                       if name != "word/styles.xml"}
        with zipfile.ZipFile(truncated, "w") as zf:
            for name, data in payload.items():
                zf.writestr(name, data)
        result = self.run_script("validate", str(truncated))
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing parts", result.stderr)


class OutlineTest(ReportCase):
    def test_outline_default_sections(self) -> None:
        out = Path(self.tmp.name, "outline.md")
        result = self.run_script("outline", "-o", str(out),
                                 "--title", "Q3 Review")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Wrote {} (5 sections)".format(out), result.stdout)
        text = out.read_text(encoding="utf-8")
        self.assertIn("# Q3 Review", text)
        for section in ("Summary", "Background", "Findings",
                        "Recommendations", "Appendix"):
            self.assertIn(f"## {section}", text)

    def test_outline_extra_section(self) -> None:
        out = Path(self.tmp.name, "outline2.md")
        result = self.run_script("outline", "-o", str(out),
                                 "--section", "Risks", "--section", "Costs")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = out.read_text(encoding="utf-8")
        self.assertIn("## Risks", text)
        self.assertIn("## Costs", text)


if __name__ == "__main__":
    unittest.main()