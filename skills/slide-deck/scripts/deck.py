#!/usr/bin/env python3
"""HTML slide-deck builder: a Markdown outline -> one self-contained .html.

Slides are separated by a line that is exactly `---`. The output file has
all CSS and JS inline, needs no network and no build step, supports
themes, keyboard navigation, speaker notes (kept off the slide face)
and printing to PDF via the browser.

Standard library only -- `python3 scripts/deck.py --help` works with
zero setup. All source text is HTML-escaped; nothing is interpolated
raw.

Adapted from lewislulu/html-ppt-skill (MIT); this implementation is
original. See LICENSE and SKILL.md for attribution.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

THEMES: dict[str, dict[str, str]] = {
    "minimal": {
        "--bg": "#ffffff", "--fg": "#1a1a1a", "--muted": "#5f6368",
        "--accent": "#2563eb", "--code-bg": "#f1f3f4",
        "--body-font": "system-ui, sans-serif",
        "--title-font": "system-ui, sans-serif",
    },
    "editorial": {
        "--bg": "#faf7f2", "--fg": "#262220", "--muted": "#7a6a5f",
        "--accent": "#b3541e", "--code-bg": "#efe9e0",
        "--body-font": "Georgia, 'Times New Roman', serif",
        "--title-font": "Georgia, 'Times New Roman', serif",
    },
    "midnight": {
        "--bg": "#0f1220", "--fg": "#e8eaf2", "--muted": "#9aa1b5",
        "--accent": "#7c9cff", "--code-bg": "#1c2033",
        "--body-font": "system-ui, sans-serif",
        "--title-font": "system-ui, sans-serif",
    },
    "terminal": {
        "--bg": "#0c0c0c", "--fg": "#c7fcc4", "--muted": "#6a9c67",
        "--accent": "#ffd75f", "--code-bg": "#141414",
        "--body-font": "'DejaVu Sans Mono', Menlo, monospace",
        "--title-font": "'DejaVu Sans Mono', Menlo, monospace",
    },
    "swiss": {
        "--bg": "#ffffff", "--fg": "#000000", "--muted": "#606060",
        "--accent": "#d5261e", "--code-bg": "#efefef",
        "--body-font": "'Helvetica Neue', Arial, sans-serif",
        "--title-font": "'Helvetica Neue', Arial, sans-serif",
    },
}

FENCE_OPEN_RE = re.compile(r"^(`{3,})(\S*)\s*$")
META_RE = re.compile(r"^(Title|Author|Theme):\s*(.*)$")
NOTES_RE = re.compile(r"^>\s*Notes?:\s*(.*)$", re.IGNORECASE)
HTML_NOTES_RE = re.compile(r"^<!--\s*notes?:\s*(.*?)\s*-->\s*$", re.IGNORECASE)
LAYOUT_RE = re.compile(r"^<!--\s*layout:\s*(\S+)\s*-->\s*$", re.IGNORECASE)
BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
ORDERED_RE = re.compile(r"^\d+[.)]\s+(.*)$")
CODESPAN_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^()\s]+)\)")


class DeckError(Exception):
    """User-facing failure: printed as `Error: <message>`, exit 1."""


# --------------------------------------------------------------------------
# parsing


def split_slides(text: str) -> list[list[str]]:
    """Source lines split into slides on a bare `---` outside code fences."""
    slides: list[list[str]] = []
    current: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        match = FENCE_OPEN_RE.match(stripped)
        if fence is None and match:
            fence = match.group(2) or ""
        elif fence is not None and stripped.startswith("```"):
            fence = None
        if stripped == "---" and fence is None:
            if any(ln.strip() for ln in current):
                slides.append(current)
            current = []
        else:
            current.append(line)
    if fence is not None:
        raise DeckError("unterminated code fence in source")
    if any(ln.strip() for ln in current):
        slides.append(current)
    return slides


def inline(text: str) -> str:
    """Escape, then apply the inline span rules. Never returns raw input."""
    out = html.escape(text, quote=True)
    out = IMAGE_RE.sub(
        lambda m: '<img src="{}" alt="{}">'.format(
            html.escape(m.group(2), quote=True), html.escape(m.group(1))),
        out)
    out = CODESPAN_RE.sub(r"<code>\1</code>", out)
    out = BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = ITALIC_RE.sub(r"<em>\1</em>", out)
    return out


class Slide:
    def __init__(self) -> None:
        self.title: str | None = None
        self.layout: str | None = None
        self.notes: list[str] = []
        self.blocks: list[tuple[str, object]] = []
        self.meta: dict[str, str] = {}


def parse_slide(lines: list[str]) -> Slide:
    slide = Slide()
    fence: str | None = None
    code_lines: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None
    list_items: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            slide.blocks.append(
                ("p", inline(" ".join(paragraph).strip())))
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_kind
        if list_kind:
            slide.blocks.append((list_kind, list(inline(i) for i in list_items)))
            list_items.clear()
            list_kind = None

    for line in lines:
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith("```"):
                slide.blocks.append(
                    ("code", (fence, html.escape("\n".join(code_lines)))))
                code_lines.clear()
                fence = None
            else:
                code_lines.append(line)
            continue
        match = FENCE_OPEN_RE.match(stripped)
        if match:
            flush_paragraph()
            flush_list()
            fence = match.group(2) or ""
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        match = META_RE.match(stripped)
        if match and not slide.blocks and slide.title is None:
            slide.meta[match.group(1).lower()] = match.group(2).strip()
            continue
        match = HTML_NOTES_RE.match(stripped)
        if match:
            slide.notes.append(match.group(1))
            continue
        match = LAYOUT_RE.match(stripped)
        if match:
            slide.layout = match.group(1)
            continue
        match = NOTES_RE.match(stripped)
        if match:
            note = match.group(1)
            if slide.notes:
                slide.notes[-1] += " " + note
            else:
                slide.notes.append(note)
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            slide.blocks.append(("quote", inline(stripped.lstrip("> "))))
            continue
        if stripped.startswith("# "):
            flush_paragraph()
            flush_list()
            if slide.title is None:
                slide.title = stripped[2:].strip()
            else:
                slide.blocks.append(("h", (1, inline(stripped[2:].strip()))))
            continue
        if stripped.startswith("## "):
            flush_paragraph()
            flush_list()
            text = stripped[3:].strip()
            if slide.title is None:
                # A slide whose only heading is `##` uses it as the title.
                slide.title = text
            else:
                slide.blocks.append(("h", (2, inline(text))))
            continue
        match = BULLET_RE.match(stripped)
        if match:
            flush_paragraph()
            if list_kind == "ul":
                list_items.append(match.group(1))
            else:
                flush_list()
                list_kind = "ul"
                list_items.append(match.group(1))
            continue
        match = ORDERED_RE.match(stripped)
        if match:
            flush_paragraph()
            if list_kind == "ol":
                list_items.append(match.group(1))
            else:
                flush_list()
                list_kind = "ol"
                list_items.append(match.group(1))
            continue
        paragraph.append(stripped)
    if fence is not None:
        raise DeckError("unterminated code fence in source")
    flush_paragraph()
    flush_list()
    return slide


def parse_deck(text: str) -> list[Slide]:
    return [parse_slide(lines) for lines in split_slides(text)]


# --------------------------------------------------------------------------
# rendering


def render_block(kind: str, payload: object) -> str:
    if kind == "p":
        return f"<p>{payload}</p>"
    if kind == "quote":
        return f"<blockquote>{payload}</blockquote>"
    if kind == "code":
        lang, body = payload  # type: ignore[misc]
        cls = f' class="language-{html.escape(lang)}"' if lang else ""
        return f"<pre><code{cls}>{body}</code></pre>"
    if kind == "h":
        level, text = payload  # type: ignore[misc]
        return f"<h{level}>{text}</h{level}>"
    if kind in ("ul", "ol"):
        items = "".join(f"<li>{item}</li>" for item in payload)  # type: ignore[misc]
        return f"<{kind}>{items}</{kind}>"
    return ""


def render_section(index: int, slide: Slide) -> str:
    classes = ["slide"]
    if slide.layout:
        classes.append("layout-" + html.escape(slide.layout))
    if index == 0:
        classes.append("title-slide")
    level = "h1" if index == 0 else "h2"
    title = slide.title or ""
    parts = [f'<section class="{" ".join(classes)}" data-index="{index}"',
             ' aria-label="Slide {}">'.format(index + 1)]
    if slide.title is not None:
        parts.append(f"<{level}>{inline(title)}</{level}>")
    parts.extend(render_block(kind, payload) for kind, payload in slide.blocks)
    parts.append("</section>")
    return "".join(parts)


def theme_rules() -> str:
    blocks = []
    for name, tokens in THEMES.items():
        body = "".join(f"    {key}: {value};\n" for key, value in tokens.items())
        blocks.append(f"body.theme-{name} {{\n{body}}}")
    return "\n".join(blocks)


STYLES = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; height: 100%; }
body {
  background: var(--bg); color: var(--fg);
  font-family: var(--body-font); overflow: hidden;
}
#deck { height: 100vh; }
section.slide {
  display: none; height: 100vh; padding: 8vh 10vw;
  flex-direction: column; justify-content: center;
}
section.slide.active { display: flex; }
body.overview section.slide {
  display: block; height: auto; min-height: 120px; padding: 16px;
  border: 1px solid var(--muted); margin: 8px; font-size: 0.9rem;
}
h1, h2 { font-family: var(--title-font); color: var(--fg); }
h1 { font-size: 2.6rem; margin: 0 0 2rem; }
h2 { font-size: 2rem; margin: 0 0 1.5rem; }
section h2 { border-bottom: 4px solid var(--accent); padding-bottom: 0.5rem; }
p { font-size: 1.35rem; line-height: 1.6; }
blockquote {
  font-size: 1.6rem; font-style: italic; margin: 0 4vw;
  border-left: 6px solid var(--accent); padding-left: 2rem; color: var(--fg);
}
pre {
  background: var(--code-bg); padding: 1rem; border-radius: 8px;
  overflow: auto; font-size: 1rem;
}
code { font-family: ui-monospace, monospace; }
ul, ol { font-size: 1.35rem; line-height: 1.7; }
section.slide.layout-title { text-align: center; }
section.slide.layout-title h1, section.slide.layout-title h2 { font-size: 3rem; }
section.slide.layout-quote h2 { display: none; }
img { max-width: 70%; max-height: 60vh; display: block; margin: 0 auto; }
#chrome {
  position: fixed; bottom: 16px; right: 16px; display: flex; gap: 8px;
  align-items: center; z-index: 20;
}
#chrome button {
  background: var(--code-bg); color: var(--fg); border: 1px solid var(--muted);
  border-radius: 6px; padding: 6px 12px; font-size: 1rem; cursor: pointer;
}
#counter { color: var(--muted); font-size: 0.9rem; }
#progress { position: fixed; bottom: 0; left: 0; width: 100%; height: 4px; background: var(--code-bg); }
#progress div { height: 100%; width: 0; background: var(--accent); }
#notes-panel {
  position: fixed; bottom: 48px; left: 16px; right: 16px; max-width: 60ch;
  background: var(--code-bg); color: var(--fg); padding: 12px 16px;
  border: 1px solid var(--muted); border-radius: 8px; z-index: 30;
}
#notes-panel[hidden] { display: none; }
#help {
  position: fixed; inset: 0; display: flex; align-items: center;
  justify-content: center; background: color-mix(in srgb, var(--bg) 90%, transparent);
  z-index: 40;
}
#help[hidden] { display: none; }
#help table {
  background: var(--code-bg); border-collapse: collapse; padding: 2rem;
  color: var(--fg);
}
#help td { border: 1px solid var(--muted); padding: 8px 16px; }
@media (prefers-reduced-motion: no-preference) {
  section.slide.active { animation: fade 160ms ease-out; }
  @keyframes fade { from { opacity: 0.4; } to { opacity: 1; } }
}
@media print {
  body { overflow: visible; background: var(--bg); }
  #deck { height: auto; }
  section.slide {
    display: flex; height: auto; min-height: 95vh; page-break-after: always;
  }
  #chrome, #progress, #notes-panel, #help { display: none; }
}
"""

SCRIPT = """
(function () {
  var slides = [].slice.call(document.querySelectorAll('section.slide'));
  var notes = [].slice.call(document.querySelectorAll('aside.notes'));
  var counter = document.getElementById('counter');
  var progress = document.getElementById('progress-fill');
  var notesPanel = document.getElementById('notes-panel');
  var help = document.getElementById('help');
  var themes = __THEMES__;
  var current = 0;

  function clamp(n) { return Math.max(0, Math.min(slides.length - 1, n)); }

  function show(n) {
    current = clamp(n);
    slides.forEach(function (s, i) {
      s.classList.toggle('active', i === current);
      s.setAttribute('aria-hidden', i === current ? 'false' : 'true');
    });
    counter.textContent = (current + 1) + ' / ' + slides.length;
    progress.style.width = (100 * (current + 1) / slides.length) + '%';
    if (!notesPanel.hidden) {
      notesPanel.textContent = notes[current] ? notes[current].textContent : '';
    }
    if (location.hash !== '#/' + (current + 1)) {
      location.hash = '#/' + (current + 1);
    }
  }

  function cycleTheme() {
    var body = document.body;
    var used = themes.filter(function (t) { return body.classList.contains('theme-' + t); });
    var index = used.length ? themes.indexOf(used[0]) : -1;
    themes.forEach(function (t) { body.classList.remove('theme-' + t); });
    body.classList.add('theme-' + themes[(index + 1) % themes.length]);
  }

  document.addEventListener('keydown', function (event) {
    if (event.metaKey || event.ctrlKey || event.altKey) { return; }
    switch (event.key) {
      case 'ArrowRight': case 'PageDown': case ' ': show(current + 1); event.preventDefault(); break;
      case 'ArrowLeft': case 'PageUp': show(current - 1); event.preventDefault(); break;
      case 'Home': show(0); break;
      case 'End': show(slides.length - 1); break;
      case 't': cycleTheme(); break;
      case 'f':
        if (document.fullscreenElement) { document.exitFullscreen(); }
        else if (document.documentElement.requestFullscreen) { document.documentElement.requestFullscreen(); }
        break;
      case 'o': document.body.classList.toggle('overview'); break;
      case 'n':
        notesPanel.hidden = !notesPanel.hidden;
        if (!notesPanel.hidden) { notesPanel.textContent = notes[current] ? notes[current].textContent : ''; }
        break;
      case '?': help.hidden = !help.hidden; break;
      case 'Escape':
        help.hidden = true; notesPanel.hidden = true;
        document.body.classList.remove('overview');
        break;
    }
  });

  document.getElementById('prev').addEventListener('click', function () { show(current - 1); });
  document.getElementById('next').addEventListener('click', function () { show(current + 1); });

  var deepLink = location.hash.match(/^#\\/(\\d+)/);
  show(deepLink ? parseInt(deepLink[1], 10) - 1 : 0);
  window.addEventListener('hashchange', function () {
    var m = location.hash.match(/^#\\/(\\d+)/);
    if (m) { show(parseInt(m[1], 10) - 1); }
  });
})();
""".replace("__THEMES__", json.dumps(list(THEMES)))


def render_html(slides: list[Slide], theme: str, title: str | None,
                author: str | None) -> str:
    if slides and slides[0].meta.get("title"):
        title = title or slides[0].meta["title"]
    if slides and slides[0].meta.get("author"):
        author = author or slides[0].meta["author"]
    deck_title = title or (slides[0].title if slides and slides[0].title else "Untitled deck")
    sections = "".join(render_section(i, s) for i, s in enumerate(slides))
    notes = "".join(
        f'<aside class="notes" id="notes-{i}" hidden>{html.escape(" ".join(s.notes))}</aside>'
        for i, s in enumerate(slides) if s.notes)
    author_line = f"\n<meta name=\"author\" content=\"{html.escape(author)}\">" if author else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="agent-kit slide-deck">
<title>{html.escape(deck_title)}</title>{author_line}
<style>
{theme_rules()}
{STYLES}
</style>
</head>
<body class="theme-{html.escape(theme)}" data-deck-title="{html.escape(deck_title)}">
<div id="deck" role="main">
{sections}
</div>
{notes}
<nav id="chrome" aria-label="Deck controls">
  <button id="prev" aria-label="Previous slide">&#9664;</button>
  <button id="next" aria-label="Next slide">&#9654;</button>
  <span id="counter" aria-live="polite">1 / {len(slides)}</span>
</nav>
<div id="progress" role="progressbar" aria-label="Slide progress"><div id="progress-fill"></div></div>
<div id="notes-panel" hidden aria-label="Speaker notes"></div>
<div id="help" hidden aria-label="Keyboard shortcuts">
  <table>
    <tr><td>&rarr; / Space / PageDown</td><td>next slide</td></tr>
    <tr><td>&larr; / PageUp</td><td>previous slide</td></tr>
    <tr><td>Home / End</td><td>first / last slide</td></tr>
    <tr><td>t</td><td>cycle theme</td></tr>
    <tr><td>f</td><td>fullscreen</td></tr>
    <tr><td>o</td><td>overview grid</td></tr>
    <tr><td>n</td><td>speaker notes</td></tr>
    <tr><td>?</td><td>this help</td></tr>
    <tr><td>Esc</td><td>close overlays</td></tr>
  </table>
</div>
<script>
{SCRIPT}
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# commands


def read_source(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise DeckError(f"cannot read {path}: {exc.strerror}") from None


def cmd_build(args: argparse.Namespace) -> None:
    slides = parse_deck(read_source(args.source))
    theme = args.theme
    if theme == "auto":
        meta_theme = slides[0].meta.get("theme") if slides else None
        theme = meta_theme or "minimal"
    if theme not in THEMES:
        raise DeckError(f"unknown theme: {theme} (known: {', '.join(THEMES)})")
    page = render_html(slides, theme, args.title, args.author)
    out = Path(args.output)
    try:
        out.write_text(page, encoding="utf-8")
    except OSError as exc:
        raise DeckError(f"cannot write {out}: {exc.strerror}") from None
    print(f"Wrote {out} ({len(slides)} slides, theme {theme})")


def cmd_themes(_args: argparse.Namespace) -> None:
    for name in THEMES:
        print(name)
    print(f"Themes: {len(THEMES)}")


def cmd_slides(args: argparse.Namespace) -> None:
    slides = parse_deck(read_source(args.source))
    for index, slide in enumerate(slides, start=1):
        print(f"{index}. {slide.title or '(untitled)'}")
    print(f"Slides: {len(slides)}")


# --------------------------------------------------------------------------
# CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deck.py",
        description="Build a self-contained HTML slide deck from a Markdown outline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="render source.md to a single .html file")
    p.add_argument("source", help="Markdown outline")
    p.add_argument("-o", "--output", required=True, help="output .html path")
    p.add_argument("--theme", default="minimal",
                   help="initial theme (auto = the deck's Theme: line)")
    p.add_argument("--title", help="deck title override")
    p.add_argument("--author", help="deck author override")

    sub.add_parser("themes", help="list theme names")

    p = sub.add_parser("slides", help="list slide titles and count")
    p.add_argument("source", help="Markdown outline")

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"build": cmd_build, "themes": cmd_themes, "slides": cmd_slides}
    try:
        handlers[args.command](args)
    except DeckError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))