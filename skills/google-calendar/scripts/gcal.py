#!/usr/bin/env python3
"""Google Calendar client (Google Calendar API v3).

One self-contained CLI: list, show, create, update, delete and free/busy
events for one calendar at a time.

Standard library only -- `python3 scripts/gcal.py --help` works with zero
setup. Credentials come from GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET /
GOOGLE_REFRESH_TOKEN environment variables or a credential file; they are
never printed and never written to disk. An access token is minted from
the refresh token on demand and, on a 401, re-minted exactly once per
command.

If the refresh token has been revoked the token endpoint answers
`invalid_grant` -- re-minting the refresh token needs a browser OAuth
flow, which this script deliberately does not automate.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

API_BASE = "https://www.googleapis.com/calendar/v3"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
BASE_ENV = "GOOGLE_CALENDAR_API_BASE"
TOKEN_URL_ENV = "GOOGLE_OAUTH_TOKEN_URL"
CREDENTIALS_ENV = "AGENT_KIT_CREDENTIALS"
USER_AGENT = "agent-kit-gcal/1.0"
TIMEOUT = 30

CLIENT_ID_KEY = "GOOGLE_CLIENT_ID"
CLIENT_SECRET_KEY = "GOOGLE_CLIENT_SECRET"
REFRESH_TOKEN_KEY = "GOOGLE_REFRESH_TOKEN"
CALENDAR_ID_KEY = "GOOGLE_CALENDAR_ID"
TIMEZONE_KEY = "GOOGLE_CALENDAR_TIMEZONE"

MISSING_CREDENTIALS = (
    "missing Google credentials (set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,"
    " GOOGLE_REFRESH_TOKEN or a credential file)"
)

DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
DATETIME_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?"
    r"(Z|[+-]\d{2}:\d{2})?$"
)
EXPORT_RE = re.compile(r"^export\s+")


class GcalError(Exception):
    """User-facing failure: printed as `Error: <message>`, exit 1."""


# --------------------------------------------------------------------------
# credentials


def credential_files() -> list[Path]:
    """Credential files in resolution order (first match wins per key)."""
    paths: list[Path] = []
    override = os.environ.get(CREDENTIALS_ENV)
    if override:
        paths.append(Path(override).expanduser())
    credentials = Path.home() / ".config" / "agent-kit" / "credentials"
    paths.append(credentials / "google-calendar.env")
    paths.append(credentials / "personal-assistant.env")
    return paths


def parse_credential_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines: `#` comments and blank lines are skipped."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = EXPORT_RE.sub("", line, count=1)
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def resolve_setting(key: str) -> str | None:
    """Environment first, then the credential files in order."""
    value = os.environ.get(key, "").strip()
    if value:
        return value
    for path in credential_files():
        value = parse_credential_file(path).get(key, "").strip()
        if value:
            return value
    return None


class Credentials:
    """The three keys OAuth needs, plus the calendar defaults."""

    def __init__(self) -> None:
        self.client_id = resolve_setting(CLIENT_ID_KEY)
        self.client_secret = resolve_setting(CLIENT_SECRET_KEY)
        self.refresh_token = resolve_setting(REFRESH_TOKEN_KEY)
        calendar_id = resolve_setting(CALENDAR_ID_KEY)
        self.calendar_id = calendar_id or "primary"
        self.timezone = resolve_setting(TIMEZONE_KEY)

    def require(self) -> None:
        if not (self.client_id and self.client_secret and self.refresh_token):
            raise GcalError(MISSING_CREDENTIALS)


# --------------------------------------------------------------------------
# time semantics
#
# A value without `T` is an all-day date. A value with `T` is a moment:
# one carrying `Z` or an offset is used as-is; a naive one is interpreted
# in --timezone (else GOOGLE_CALENDAR_TIMEZONE, else the host's local
# zone) and converted to RFC 3339 with the right offset. All-day events
# are exclusive on the far end: a one-day event ends the next day.


def resolve_zone(name: str | None):
    """A named zone as a tzinfo, or None for the host's local offset."""
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        raise GcalError(f"unknown timezone: {name}") from None


def parse_when(spec: str, zone_name: str | None) -> dict:
    """`--start/--end` value -> `{"date": ...}` or `{"dateTime": ...}`."""
    raw = spec.strip()
    if DATE_RE.match(raw):
        return {"date": raw}
    match = DATETIME_RE.match(raw)
    if not match:
        raise GcalError(f"cannot parse time: {spec}")
    try:
        moment = datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3)),
            int(match.group(4)), int(match.group(5)), int(match.group(6) or 0),
        )
    except ValueError:
        raise GcalError(f"cannot parse time: {spec}") from None
    offset = match.group(7)
    if offset == "Z":
        stamp = moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment.second else \
            moment.strftime("%Y-%m-%dT%H:%MZ")
        return {"dateTime": stamp}
    if offset:
        moment = datetime(
            moment.year, moment.month, moment.day, moment.hour, moment.minute,
            moment.second,
        )
        return {"dateTime": f"{moment.isoformat(timespec='seconds')}{offset}"}
    zone = resolve_zone(zone_name)
    if zone is not None:
        moment = moment.replace(tzinfo=zone)
    else:
        # No zone given: attach the host's local offset.
        moment = moment.astimezone()
    return {"dateTime": moment.isoformat(timespec="seconds")}


def parse_bound(spec: str, zone_name: str | None, end: bool) -> str:
    """`--from/--to` value -> RFC 3339 bound.

    A date-only bound widens to midnight (start) or 23:59:59 (end) of
    that local day; a full timestamp is used as given.
    """
    raw = spec.strip()
    if DATE_RE.match(raw):
        when = parse_when(raw, zone_name)
        if end:
            stamp = f"{raw}T23:59:59"
        else:
            stamp = f"{raw}T00:00:00"
        zone = resolve_zone(zone_name)
        moment = datetime.fromisoformat(stamp)
        if zone is not None:
            moment = moment.replace(tzinfo=zone)
        else:
            moment = moment.astimezone()
        return moment.isoformat(timespec="seconds")
    when = parse_when(raw, zone_name)
    return when["dateTime"]


def all_day_end(day: str) -> str:
    """The exclusive end date of a single all-day event."""
    moment = date.fromisoformat(day) + timedelta(days=1)
    return moment.isoformat()


# --------------------------------------------------------------------------
# HTTP


def api_base() -> str:
    return os.environ.get(BASE_ENV, API_BASE).rstrip("/")


def token_url() -> str:
    return os.environ.get(TOKEN_URL_ENV, OAUTH_TOKEN_URL)


def error_message(body: bytes) -> str:
    """Pull a human-readable message out of an error body."""
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except ValueError:
        return text.splitlines()[0]
    if isinstance(payload, dict):
        value = payload.get("error_description") or payload.get("error")
        if isinstance(value, str) and value:
            return value
        nested = payload.get("error")
        if isinstance(nested, dict) and isinstance(nested.get("message"), str):
            return nested["message"]
        if isinstance(payload.get("message"), str):
            return payload["message"]
    return text.splitlines()[0]


def mint_token(creds: Credentials) -> str:
    """Exchange the refresh token for a fresh access token."""
    form = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }).encode()
    request = urllib.request.Request(
        token_url(),
        data=form,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GcalError(
            f"token refresh failed: {error_message(exc.read()) or exc.reason}"
        ) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GcalError(f"token endpoint unreachable: {exc}") from None
    token = payload.get("access_token")
    if not token:
        raise GcalError("token refresh failed: no access_token in response")
    return token


class Api:
    """Calendar calls with one automatic re-mint on HTTP 401."""

    def __init__(self, creds: Credentials) -> None:
        self.creds = creds
        self.token: str | None = None
        self.reminted = False

    def authorize(self) -> str:
        if self.token is None:
            self.token = mint_token(self.creds)
        return self.token

    def request(
        self,
        method: str,
        path: str,
        query: dict | None = None,
        body: dict | None = None,
    ) -> dict:
        url = api_base() + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        for attempt in (1, 2):
            headers = {
                "Authorization": f"Bearer {self.authorize()}",
                "User-Agent": USER_AGENT,
            }
            data = None
            if body is not None:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(url, data=data, headers=headers)
            request.get_method = lambda: method  # type: ignore[method-assign]
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    text = response.read().decode("utf-8")
                return json.loads(text) if text.strip() else {}
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 1 and not self.reminted:
                    self.token = None
                    self.reminted = True
                    continue
                message = error_message(exc.read()) or exc.reason
                raise GcalError(f"HTTP {exc.code} {message}") from None
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise GcalError(f"request failed: {exc}") from None
        raise GcalError("request failed")  # unreachable


def events_path(creds: Credentials, event_id: str | None = None) -> str:
    calendar = urllib.parse.quote(creds.calendar_id, safe="")
    path = f"/calendars/{calendar}/events"
    if event_id:
        path += "/" + urllib.parse.quote(event_id, safe="")
    return path


# --------------------------------------------------------------------------
# output


def describe_event(event: dict) -> str:
    start = event.get("start") or {}
    summary = event.get("summary") or "(no title)"
    if "date" in start:
        return f"{start['date']}  all-day  {summary}"
    stamp = str(start.get("dateTime", "")).replace("T", " ")[:16]
    return f"{stamp}  {summary}"


def print_events(events: list, label: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps(events, ensure_ascii=False))
    else:
        for event in events:
            print(describe_event(event))
        print(f"{label}: {len(events)}")


# --------------------------------------------------------------------------
# commands


def fetch_window(
    api: Api,
    creds: Credentials,
    time_min: str,
    time_max: str,
    label: str,
    limit: int | None,
    as_json: bool,
) -> list:
    query: dict = {
        "timeMin": time_min,
        "timeMax": time_max,
        "orderBy": "startTime",
        "singleEvents": "true",
    }
    if limit:
        query["maxResults"] = limit
    payload = api.request("GET", events_path(creds), query=query)
    events = payload.get("items", [])
    print_events(events, label, as_json)
    return events


def cmd_auth_status(creds: Credentials, _args: argparse.Namespace) -> None:
    creds.require()
    api = Api(creds)
    api.authorize()
    print(f"Credentials: ok (calendar {creds.calendar_id})")


def cmd_list(creds: Credentials, args: argparse.Namespace) -> None:
    creds.require()
    api = Api(creds)
    zone = args.timezone or creds.timezone
    now = datetime.now().astimezone()
    from_value = getattr(args, "from")
    time_min = parse_bound(from_value or now.isoformat(timespec="seconds"),
                           zone, end=False)
    time_max = parse_bound(args.to or (now + timedelta(days=7)).isoformat(
        timespec="seconds"), zone, end=False)
    calendar = args.calendar or creds.calendar_id
    creds.calendar_id = calendar
    fetch_window(api, creds, time_min, time_max, "Events", args.max, args.json)


def cmd_today(creds: Credentials, args: argparse.Namespace) -> None:
    creds.require()
    api = Api(creds)
    zone_name = args.timezone or creds.timezone
    zone = resolve_zone(zone_name)
    day = datetime.now().astimezone()
    if zone is not None:
        day = day.astimezone(zone)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = day.replace(hour=23, minute=59, second=59, microsecond=0)
    if args.calendar:
        creds.calendar_id = args.calendar
    fetch_window(api, creds, start.isoformat(timespec="seconds"),
                 end.isoformat(timespec="seconds"), "Today", None, args.json)


def cmd_upcoming(creds: Credentials, args: argparse.Namespace) -> None:
    creds.require()
    api = Api(creds)
    zone = args.timezone or creds.timezone
    now = datetime.now().astimezone()
    time_min = now.isoformat(timespec="seconds")
    time_max = (now + timedelta(days=args.days)).isoformat(timespec="seconds")
    if args.calendar:
        creds.calendar_id = args.calendar
    fetch_window(api, creds, time_min, time_max, "Upcoming", args.max,
                 args.json)


def cmd_get(creds: Credentials, args: argparse.Namespace) -> None:
    creds.require()
    api = Api(creds)
    if args.calendar:
        creds.calendar_id = args.calendar
    payload = api.request("GET", events_path(creds, args.event_id))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Event: {args.event_id} - {payload.get('summary') or '(no title)'}")


def cmd_create(creds: Credentials, args: argparse.Namespace) -> None:
    creds.require()
    api = Api(creds)
    zone = args.timezone or creds.timezone
    if args.all_day:
        start = {"date": args.date}
        end = {"date": all_day_end(args.date)}
    else:
        if not args.end:
            raise GcalError("timed events need --end (or use --all-day --date)")
        start = parse_when(args.start, zone)
        end = parse_when(args.end, zone)
    body: dict = {"summary": args.summary, "start": start, "end": end}
    if args.description:
        body["description"] = args.description
    if args.location:
        body["location"] = args.location
    if args.attendee:
        body["attendees"] = [{"email": email} for email in args.attendee]
    if args.calendar:
        creds.calendar_id = args.calendar
    payload = api.request("POST", events_path(creds), body=body)
    print(f"Created: {payload.get('id') or args.summary}")


def cmd_update(creds: Credentials, args: argparse.Namespace) -> None:
    creds.require()
    api = Api(creds)
    zone = args.timezone or creds.timezone
    body: dict = {}
    if args.summary:
        body["summary"] = args.summary
    if args.start:
        body["start"] = parse_when(args.start, zone)
    if args.end:
        body["end"] = parse_when(args.end, zone)
    if args.description:
        body["description"] = args.description
    if args.location:
        body["location"] = args.location
    if not body:
        raise GcalError("update needs at least one --summary/--start/--end/"
                        "--description/--location")
    if args.calendar:
        creds.calendar_id = args.calendar
    api.request("PATCH", events_path(creds, args.event_id), body=body)
    print(f"Updated: {args.event_id}")


def cmd_delete(creds: Credentials, args: argparse.Namespace) -> None:
    if not args.yes:
        raise GcalError("refusing to delete an event without --yes")
    creds.require()
    api = Api(creds)
    if args.calendar:
        creds.calendar_id = args.calendar
    api.request("DELETE", events_path(creds, args.event_id))
    print(f"Deleted: {args.event_id}")


def cmd_freebusy(creds: Credentials, args: argparse.Namespace) -> None:
    creds.require()
    api = Api(creds)
    zone = args.timezone or creds.timezone
    body = {
        "timeMin": parse_bound(getattr(args, "from"), zone, end=False),
        "timeMax": parse_bound(args.to, zone, end=False),
        "items": [{"id": args.calendar or creds.calendar_id}],
    }
    payload = api.request("POST", "/freeBusy", body=body)
    calendars = payload.get("calendars", {})
    blocks: list = []
    for calendar, info in calendars.items():
        blocks.extend(info.get("busy", []))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for block in blocks:
            start = str(block.get("start", "")).replace("T", " ")[:16]
            end = str(block.get("end", "")).replace("T", " ")[:16]
            print(f"Busy: {start} - {end}")
        print(f"Freebusy: {len(blocks)}")


# --------------------------------------------------------------------------
# CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gcal.py",
        description="Read and manage Google Calendar events (Calendar API v3).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text)

    add("auth-status", "check that credentials work and a token mints")

    p = add("list", "events in a time window (default now .. +7 days)")
    p.add_argument("--from", dest="from", metavar="TIME",
                   help="window start: YYYY-MM-DD or RFC 3339")
    p.add_argument("--to", metavar="TIME", help="window end")
    p.add_argument("--max", type=int, help="limit the number of events")
    p.add_argument("--calendar", help="calendar id (default: primary)")
    p.add_argument("--timezone", help="interpret naive times in this zone")
    p.add_argument("--json", action="store_true", help="print one JSON line")

    p = add("today", "events in the local day")
    p.add_argument("--calendar", help="calendar id")
    p.add_argument("--timezone", help="local-day zone override")
    p.add_argument("--json", action="store_true", help="print one JSON line")

    p = add("upcoming", "events from now to +N days")
    p.add_argument("--days", type=int, default=7, help="window size in days")
    p.add_argument("--max", type=int, help="limit the number of events")
    p.add_argument("--calendar", help="calendar id")
    p.add_argument("--timezone", help="interpret naive times in this zone")
    p.add_argument("--json", action="store_true", help="print one JSON line")

    p = add("get", "fetch one event")
    p.add_argument("event_id")
    p.add_argument("--calendar", help="calendar id")
    p.add_argument("--json", action="store_true", help="print one JSON line")

    p = add("create", "add an event")
    p.add_argument("--summary", required=True, help="event title")
    p.add_argument("--start", metavar="TIME",
                   help="timed start: RFC 3339 or YYYY-MM-DDTHH:MM")
    p.add_argument("--end", metavar="TIME", help="timed end")
    p.add_argument("--all-day", action="store_true",
                   help="make an all-day event instead of a timed one")
    p.add_argument("--date", metavar="YYYY-MM-DD",
                   help="the day for --all-day")
    p.add_argument("--description", help="event description")
    p.add_argument("--location", help="event location")
    p.add_argument("--attendee", action="append", metavar="EMAIL",
                   help="invite an attendee (repeatable)")
    p.add_argument("--timezone", help="interpret naive times in this zone")
    p.add_argument("--calendar", help="calendar id")

    p = add("update", "change fields of one event (only named fields move)")
    p.add_argument("event_id")
    p.add_argument("--summary", help="new title")
    p.add_argument("--start", metavar="TIME", help="new start")
    p.add_argument("--end", metavar="TIME", help="new end")
    p.add_argument("--description", help="new description")
    p.add_argument("--location", help="new location")
    p.add_argument("--timezone", help="interpret naive times in this zone")
    p.add_argument("--calendar", help="calendar id")

    p = add("delete", "remove an event (requires --yes)")
    p.add_argument("event_id")
    p.add_argument("--yes", action="store_true",
                   help="confirm the destructive operation")
    p.add_argument("--calendar", help="calendar id")

    p = add("freebusy", "busy blocks in a window")
    p.add_argument("--from", dest="from", required=True, metavar="TIME",
                   help="window start: YYYY-MM-DD or RFC 3339")
    p.add_argument("--to", required=True, metavar="TIME", help="window end")
    p.add_argument("--calendar", help="calendar id")
    p.add_argument("--timezone", help="interpret naive times in this zone")
    p.add_argument("--json", action="store_true", help="print one JSON line")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    creds = Credentials()
    handlers = {
        "auth-status": cmd_auth_status,
        "list": cmd_list,
        "today": cmd_today,
        "upcoming": cmd_upcoming,
        "get": cmd_get,
        "create": cmd_create,
        "update": cmd_update,
        "delete": cmd_delete,
        "freebusy": cmd_freebusy,
    }
    try:
        handlers[args.command](creds, args)
    except GcalError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))