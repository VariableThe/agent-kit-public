---
name: google-calendar
description: Read and manage Google Calendar events (list, agenda, free/busy, create, update, delete). Triggers: google calendar, gcal, calendar events, agenda, schedule a meeting, freebusy, my calendar.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: stdlib-only
---

# Google Calendar

One entry point for Google Calendar: `scripts/gcal.py`. It talks the
Calendar API v3 for one calendar at a time — list, inspect, create,
update, delete and free/busy — and never invents an endpoint.

Standard library only — `python3 scripts/gcal.py --help` works with zero
setup.

## Purpose

- Discover: `auth-status` (credentials present, token mintable).
- Read: `list`, `today`, `upcoming`, `get`, `freebusy`.
- Write: `create`, `update`, `delete`.

## Setup & credentials

The script needs an OAuth client and a refresh token:

- `GOOGLE_CLIENT_ID` — OAuth client id.
- `GOOGLE_CLIENT_SECRET` — OAuth client secret.
- `GOOGLE_REFRESH_TOKEN` — a valid refresh token.
- `GOOGLE_CALENDAR_ID` — optional, defaults to `primary`.
- `GOOGLE_CALENDAR_TIMEZONE` — optional, e.g. `Europe/Berlin`; used to
  interpret naive times and local-day boundaries.

Each key resolves environment first, then credential files in this
order: `$AGENT_KIT_CREDENTIALS` (when set),
`~/.config/agent-kit/credentials/google-calendar.env`, then
`~/.config/agent-kit/credentials/personal-assistant.env`. Copy
`credentials.example.env` to one of those paths, fill it in, and
`chmod 600` it. Credentials are never printed and never written to disk.

**Refresh tokens expire.** If the token endpoint answers `invalid_grant`,
the stored refresh token has been revoked or expired. Re-minting it needs
a browser OAuth flow (consent screen with the calendar scope, loopback
redirect, offline access) — this script deliberately does not automate
that. When it happens, re-mint via your OAuth tooling and update the
credential file.

## Commands

| Command | Effect |
| --- | --- |
| `auth-status` | verify credentials and that a token mints |
| `list [--from T] [--to T] [--max N] [--calendar ID] [--json]` | events in a window (default now .. +7 days) |
| `today [--calendar ID] [--json]` | events in the local day |
| `upcoming [--days N] [--calendar ID] [--json]` | events from now to +N days |
| `get <eventId> [--json]` | one event |
| `create --summary S (--start T --end T \| --all-day --date D) [...]` | add an event |
| `update <eventId> [--summary] [--start] [--end] [...]` | change only the named fields |
| `delete <eventId> --yes` | remove an event (destructive, needs `--yes`) |
| `freebusy --from T --to T [--calendar ID]` | busy blocks in a window |

## Examples

```console
$ python3 scripts/gcal.py auth-status
Credentials: ok (calendar primary)

$ python3 scripts/gcal.py today
2026-03-04 09:30  Standup
2026-03-04 14:00  Design review
Today: 2

$ python3 scripts/gcal.py create --summary "Dentist" \
    --start 2026-03-05T10:00 --end 2026-03-05T11:00 --timezone Europe/Berlin
Created: abc123

$ python3 scripts/gcal.py create --summary "Offsite" --all-day --date 2026-03-09
Created: def456

$ python3 scripts/gcal.py freebusy --from 2026-03-04 --to 2026-03-04
Busy: 2026-03-04 09:30 - 2026-03-04 10:00
Freebusy: 1
```

## Time semantics

- A value without `T` (`2026-03-04`) is a **date**: all-day events use
  `date` fields, and `end.date` is **exclusive** — a one-day event on
  March 4 ends March 5.
- A value with `T` is a **moment**. With `Z` or an offset it is used
  as-is; naive values are interpreted in `--timezone`, else
  `GOOGLE_CALENDAR_TIMEZONE`, else the host's local zone, and sent as
  RFC 3339 with the correct offset.
- `--from`/`--to` accept dates or timestamps; a date-only bound widens
  to midnight (start) or end-of-day 23:59:59 (end) of that local day.

## Rules

- One short summary line per successful command (`Events: 12`,
  `Created: <id>`); `--json` prints one compact JSON line instead.
- Failure: one `Error: <message>` line on stderr, exit 1. argparse usage
  errors exit 2.
- `delete` refuses to run without `--yes`.
- On HTTP 401 the token is re-minted **once** per command, then the
  request is retried; a second 401 surfaces as an error.
- No secret value is ever printed, logged or written to disk; the access
  token lives only in process memory for one command.
- The API base URL and token endpoint can be overridden with
  `GOOGLE_CALENDAR_API_BASE` / `GOOGLE_OAUTH_TOKEN_URL` for testing.