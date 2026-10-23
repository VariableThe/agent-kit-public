# Agent Rules — skills/

Applies to everything under `skills/`. Overrides the root `AGENTS.md` on
conflict.

## Scope

- Skills are content plus self-contained scripts. No builds, no servers.
- One concern per skill. Don't bundle unrelated operations into one skill.
- `SKILL.md` stays under 500 lines; frontmatter `name` matches the directory.

## Scripts

- Every script runs as `python3 scripts/<name>.py --help` with zero setup
  beyond `pip install -r requirements.txt` plus documented system binaries.
- Stdlib first. New third-party dependency needs a reason in the PR.
- Non-zero exit plus a one-line `Error:` message on failure. Print a short
  summary (`Wrote ...`, `Pages: ...`) on success.

## Adding a skill

- Create `skills/<name>/SKILL.md` and `skills/<name>/scripts/`.
- The installer links each directory under `skills/` automatically — no
  manifest to update.

## Verification

- Test every script against real generated fixtures before committing
  (build fixtures with the same libraries, not by hand).
- `python3 -m py_compile` on every changed script.
