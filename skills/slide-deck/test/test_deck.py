"""Tests for skills/slide-deck/scripts/deck.py.

The Markdown outline is generated programmatically (not a hand-written
blob) and the script runs as a real subprocess. No network access.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "skills" / "slide-deck" / "scripts" / "deck.py"

THEMES = ["minimal", "editorial", "midnight", "terminal", "swiss"]


def outline_text() -> str:
    """A generated outline exercising every feature and nasty characters."""
    hostile = "<script>alert('x')</script> & <b>raw</b>"
    lines = [
        "Title: Generated Deck",
        "Author: user@example.com",
        "Theme: midnight",
        "",
        "# First Slide",
        "",
        "> Notes: opening remarks, keep it to one minute.",
        "> Notes: continued note line",
        "",
        "---",
        "",
        "## Bullets & code",
        "",
        f"- plain bullet with {hostile}",
        "- **bold** and *italic* and `inline code`",
        "* star bullet too",
        "",
        "1. first ordered",
        "2. second ordered",
        "",
        "```python",
        "# a fence containing a slide separator",
        "---",
        "print('not a new slide')",
        "```",
        "",
        "---",
        "",
        "<!-- layout: quote -->",
        "<!-- notes: hidden note via html comment -->",
        "",
        "## Quote slide",
        "",
        "> Minimal decks win.",
        "",
        "---",
        "",
        "## Last slide",
        "",
        "![diagram](diagram.png) and a plain paragraph.",
    ]
    return "\n".join(lines) + "\n"


class DeckCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name, "slides.md")
        self.output = Path(self.tmp.name, "deck.html")
        self.source.write_text(outline_text(), encoding="utf-8")

    def run_script(self, *cli: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *cli],
            capture_output=True, text=True, timeout=60)

    def build(self, *extra: str) -> tuple[subprocess.CompletedProcess, str]:
        result = self.run_script(
            "build", str(self.source), "-o", str(self.output), *extra)
        page = self.output.read_text(encoding="utf-8") if result.returncode == 0 else ""
        return result, page


class BuildTest(DeckCase):
    def test_build_summary_and_slide_count(self) -> None:
        result, page = self.build("--theme", "editorial")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Wrote {} (4 slides, theme editorial)".format(
            self.output), result.stdout)
        self.assertEqual(page.count('<section class="'), 4)

    def test_no_external_resources(self) -> None:
        _, page = self.build()
        self.assertNotIn("<link rel=\"stylesheet\"", page)
        self.assertNotIn("<script src=", page)
        self.assertNotIn("src=\"http", page)
        self.assertNotIn("href=\"http", page)
        self.assertNotIn("@import", page)

    def test_hostile_text_escaped(self) -> None:
        _, page = self.build()
        self.assertIn("&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert", page)

    def test_notes_off_slide_face(self) -> None:
        _, page = self.build()
        # Notes exist in the file, as hidden asides outside the sections.
        self.assertIn("opening remarks", page)
        self.assertIn("hidden note via html comment", page)
        inside = False
        for line in page.splitlines():
            if "<section" in line:
                inside = True
            if "</section>" in line:
                inside = False
            if inside and ("opening remarks" in line
                          or "hidden note via html comment" in line):
                self.fail("speaker note rendered inside a slide section")

    def test_themes_present_and_cycled(self) -> None:
        _, page = self.build()
        for name in THEMES:
            self.assertIn("body.theme-{}".format(name), page)

    def test_fence_separator_is_not_a_break(self) -> None:
        _, page = self.build()
        # `---` inside the python fence stays one slide -> 4 sections total.
        self.assertEqual(page.count('<section class="'), 4)

    def test_metadata_title_author_theme(self) -> None:
        _, page = self.build("--theme", "auto")
        self.assertIn("Generated Deck", page)
        self.assertIn('<meta name="author" content="user@example.com">', page)

    def test_unknown_theme_fails(self) -> None:
        result, _ = self.build("--theme", "neon")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("Error: unknown theme: neon"),
                        result.stderr)

    def test_unterminated_fence_fails(self) -> None:
        self.source.write_text("# Slide\n\n```python\nprint(1)\n",
                               encoding="utf-8")
        result = self.run_script("build", str(self.source), "-o", str(self.output))
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("Error:"), result.stderr)


class ListingTest(DeckCase):
    def test_slides_command(self) -> None:
        result = self.run_script("slides", str(self.source))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1. First Slide", result.stdout)
        self.assertIn("2. Bullets & code", result.stdout)
        self.assertIn("3. Quote slide", result.stdout)
        self.assertIn("4. Last slide", result.stdout)
        self.assertIn("Slides: 4", result.stdout)

    def test_themes_command(self) -> None:
        result = self.run_script("themes")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in THEMES:
            self.assertIn(name, result.stdout)
        self.assertIn("Themes: 5", result.stdout)


if __name__ == "__main__":
    unittest.main()