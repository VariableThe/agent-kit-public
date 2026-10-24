---
name: slide-deck
description: Generate self-contained HTML slide decks from a Markdown outline, with themes, keyboard navigation, speaker notes and print-to-PDF. Triggers: make a deck, build slides, slide deck, presentation, keynote, pitch deck, talk slides, turn notes into slides.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: stdlib-only
  source: Adapted from lewislulu/html-ppt-skill (MIT)
---

# Slide Deck

Turn a Markdown outline into ONE self-contained `.html` deck: all CSS
and JS inline, no network, no build step. Themes, keyboard navigation,
speaker notes kept off the slide face, and Print-to-PDF in the browser.

Standard library only — `python3 scripts/deck.py --help` works with
zero setup.

## Purpose

- `build` a deck from `slides.md`.
- `slides` to list slide titles and catch outline mistakes early.
- `themes` to see the design tokens available.

## Commands

| Command | Effect |
| --- | --- |
| `build <source.md> -o <out.html> [--theme NAME] [--title T] [--author A]` | render the deck |
| `themes` | list theme names |
| `slides <source.md>` | list slide titles + count |

## Source format

- Slides are separated by a line that is exactly `---` (blank lines
  around it are fine). `---` inside a fenced code block is **not** a
  slide break.
- The first `# Heading` of a slide is its title. `## Heading` is an
  in-slide section heading.
- `- ` / `* ` bullets, `1.` ordered lists. Inline `**bold**`, `*italic*`,
  `` `code` ``.
- `![alt](path)` embeds an image — put image files next to the deck.
- Speaker notes: a `> Notes: ...` blockquote line (or `<!-- notes: ... -->`)
  inside the slide. Notes never render on the slide face; open them at
  runtime with `n`.
- Optional per-slide directive: `<!-- layout: title | bullets | quote -->`.
- The first slide may carry `Title:`, `Author:` and `Theme:` metadata
  lines.

```markdown
Title: Shipping the Agent Kit
Author: user@example.com
Theme: midnight

# Shipping the Agent Kit

> Notes: welcome, 30 seconds on status.

---

## What changed

- Four new skills
- One installer, no manifests

<!-- layout: quote -->

## Quote slide

> Minimal decks win.
```

## Themes

`minimal`, `editorial`, `midnight`, `terminal`, `swiss` — each a set of
CSS custom properties (background, foreground, accent, fonts). `--theme`
picks the initial theme; press `t` at runtime to cycle. All themes keep
readable contrast.

## Runtime keys

| Key | Effect |
| --- | --- |
| `→` / `Space` / `PageDown` | next slide |
| `←` / `PageUp` | previous slide |
| `Home` / `End` | first / last slide |
| `t` | cycle theme |
| `f` | fullscreen |
| `o` | overview grid |
| `n` | speaker notes panel |
| `?` | help overlay |
| `Esc` | close overlays |

Deep links work: `deck.html#/5` opens slide 5. On-screen prev/next
buttons are always present and focusable.

## Workflow

1. Draft the outline in Markdown (`slides.md`), one idea per slide.
2. `deck.py slides slides.md` — check the titles read like a story.
3. `deck.py build slides.md -o deck.html --theme editorial`.
4. Open `deck.html` in a browser; press `n` to rehearse with notes.
5. Print > Save as PDF for a portable copy (one slide per page).

## Quality bar

- One idea per slide; slides are prompts, not documents.
- Real content only — no placeholder lorem ipsum in a delivered deck.
- Every deck opens offline; no CDN links, no webfonts, no build step.
- If a slide needs a diagram, build it as an image file next to the
  deck and reference it — never paste a screenshot of text.

## Attribution

Adapted from [lewislulu/html-ppt-skill](https://github.com/lewislulu/html-ppt-skill)
(MIT). This implementation and documentation are original; the ideas
kept from upstream: token-based themes, a keyboard runtime, speaker
notes off the slide face, print-to-PDF, and offline static output.
Dropped from upstream: the macOS-only Chrome invocation path, CDN
webfonts, the Claude-specific install path, and Chinese-only content
templates. The upstream notice lives in `LICENSE`.

## Rules

- Failure: one `Error: <message>` line on stderr, exit 1. argparse usage
  errors exit 2. Success prints one short summary line
  (`Wrote deck.html (12 slides, theme midnight)`).
- All source text is HTML-escaped; nothing is interpolated raw.
- An unterminated code fence is an error, not a silent slide merge.
- Output must stay a single file with zero external resources.