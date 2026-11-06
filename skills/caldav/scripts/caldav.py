#!/usr/bin/env python3
"""Caldav calendar operations over WebDAV/CalDAV.

One self-contained CLI for a CalDAV server (tested against Nextcloud Calendar):
list, today, upcoming, create, update, delete, freebusy.

Standard library only. Credentials resolve in this order:
1. Environment variables CALDAV_URL, CALDAV_USER, CALDAV_PASS.
2. The [caldav] section of ~/.config/rclone/rclone.conf (url, user, pass).
   Pass is taken verbatim (plaintext or rclone-obscured; obscure decryption
   is not implemented here — set CALDAV_PASS explicitly when the conf stores
   an obscured value).
3. Falls back to the [nextcloud] section of the same file if [caldav] is
   absent and the url contains .../dav/calendars/.
"""
from __future__ import annotations

import argparse
import base64
import configparser
import email.utils
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TIMEOUT = 30
USER_AGENT = "curl/8.23.0"
DAV_NS = "{DAV:}"


class CaldavError(Exception):
    """User-facing failure: printed as Error: <msg>, exit 1."""


def _basic_token(user: str, password: str) -> str:
    raw = f"{user}:{password}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _request(url: str, method: str, *, user: str, password: str,
             body: bytes | None = None, headers: dict[str, str] | None = None) -> bytes:
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Authorization", "Basic " + _basic_token(user, password))
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read()
        text = detail.decode("utf-8", "replace").strip().splitlines()[0] if detail else ""
        raise CaldavError(f"HTTP {e.code}: {text}" if text else f"HTTP {e.code}") from None
    except OSError as exc:
        raise CaldavError(f"request failed: {exc}") from None


def _load_creds() -> tuple[str, str, str]:
    url = os.environ.get("CALDAV_URL", "")
    user = os.environ.get("CALDAV_USER", "")
    password = os.environ.get("CALDAV_PASS", "")
    if url and user and password:
        return url.rstrip("/") + "/", user, password

    rclone = Path.home() / ".config" / "rclone" / "rclone.conf"
    if rclone.exists():
        cp = configparser.ConfigParser()
        cp.read(rclone)
        for sec_name in ("caldav", "nextcloud"):
            if sec_name in cp:
                sec = cp[sec_name]
                u = sec.get("url", "").strip()
                usr = sec.get("user", "").strip()
                pw = sec.get("pass", "").strip()
                if u and usr and pw:
                    # For nextcloud section, derive calendar home if not already caldav
                    if "/dav/calendars/" not in u and "/dav/files/" in u:
                        u = u.replace("/dav/files/", "/dav/calendars/").rstrip("/") + "/personal/"
                    return u.rstrip("/") + "/", usr, pw

    raise CaldavError(
        "no credentials: set CALDAV_URL/USER/PASS or add a [caldav] section "
        "to ~/.config/rclone/rclone.conf"
    )


def _iso_to_utc_dt(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _ics_dt_to_datetime(val: str, tzname: str | None = None) -> datetime | None:
    """Parse an ICS DTSTART/DTEND value to a timezone-aware datetime."""
    val = val.strip()
    if val.endswith("Z"):
        try:
            return datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if tzname:
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(tzname)
            return datetime.strptime(val, "%Y%m%dT%H%M%S").replace(tzinfo=tz)
        except Exception:
            pass
    try:
        return datetime.strptime(val, "%Y%m%dT%H%M%S")
    except ValueError:
        try:
            return datetime.strptime(val, "%Y%m%d").replace(hour=0, minute=0, second=0)
        except ValueError:
            return None
    return None


def _parse_ics(ics: str) -> dict[str, str | None]:
    """Minimal line-oriented ICS parse for one VEVENT."""
    lines: list[str] = []
    for raw in ics.replace("\r\n", "\n").splitlines():
        if raw.startswith(" ") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    out: dict[str, str | None] = {"summary": None, "dtstart": None, "dtend": None,
                                   "uid": None, "location": None, "tzstart": None, "tzend": None}
    for line in lines:
        if line.startswith("SUMMARY:"):
            out["summary"] = line.split(":", 1)[1]
        elif line.startswith("UID:"):
            out["uid"] = line.split(":", 1)[1]
        elif line.startswith("LOCATION:"):
            out["location"] = line.split(":", 1)[1]
        elif line.startswith("DTSTART;"):
            # DTSTART;TZID=Asia/Kolkata:20261030T140000
            rest = line[len("DTSTART;"):]
            if ":" in rest:
                params, val = rest.split(":", 1)
                for p in params.split(";"):
                    if p.startswith("TZID="):
                        out["tzstart"] = p.split("=", 1)[1]
                out["dtstart"] = val
            else:
                out["dtstart"] = rest
        elif line.startswith("DTEND;"):
            rest = line[len("DTEND;"):]
            if ":" in rest:
                params, val = rest.split(":", 1)
                for p in params.split(";"):
                    if p.startswith("TZID="):
                        out["tzend"] = p.split("=", 1)[1]
                out["dtend"] = val
            else:
                out["dtend"] = rest
        elif line == "DTSTART:" and len(line) > 8:
            out["dtstart"] = line.split(":", 1)[1]
        elif line == "DTEND:" and len(line) > 6:
            out["dtend"] = line.split(":", 1)[1]
    return out


def _build_ics(summary: str, uid: str, start_iso: str, end_iso: str,
               location: str | None = None) -> str:
    dt = datetime.now(timezone.utc)
    stamp = dt.strftime("%Y%m%dT%H%M%SZ")
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso)
    # Use local time format if offset present, else assume Asia/Kolkata
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    tz = getattr(start.tzinfo, "key", None) or "Asia/Kolkata"
    start_s = start.strftime("%Y%m%dT%H%M%S")
    end_s = end.strftime("%Y%m%dT%H%M%S")
    loc_line = f"LOCATION:{location}\r\n" if location else ""
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Agent-Kit//Caldav//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"DTSTART;TZID={tz}:{start_s}\r\n"
        f"DTEND;TZID={tz}:{end_s}\r\n"
        f"DTSTAMP:{stamp}\r\n"
        f"{loc_line}"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


def _list_events(url: str, user: str, password: str) -> list[dict]:
    """Return all events in the calendar as dicts with summary, start, end, uid."""
    body = _request(url, "PROPFIND", user=user, password=password, headers={"Depth": "1"})
    root = ET.fromstring(body)
    events: list[dict] = []
    for resp in root.findall(f"{DAV_NS}response"):
        href = resp.findtext(f"{DAV_NS}href") or ""
        if href.endswith("/"):
            continue
        # Fetch the ICS
        ics_url = urllib.parse.urljoin(url, href)
        try:
            ics_data = _request(ics_url, "GET", user=user, password=password).decode("utf-8", "replace")
        except CaldavError:
            continue
        parsed = _parse_ics(ics_data)
        if parsed.get("uid") and parsed.get("dtstart"):
            start_dt = _ics_dt_to_datetime(str(parsed["dtstart"]), str(parsed.get("tzstart") or ""))
            end_dt = _ics_dt_to_datetime(str(parsed["dtend"]), str(parsed.get("tzend") or ""))
            events.append({
                "summary": parsed.get("summary") or "",
                "uid": parsed["uid"],
                "start": start_dt,
                "end": end_dt,
                "location": parsed.get("location") or "",
            })
    events.sort(key=lambda e: (e["start"] is None, e["start"] or datetime.min.replace(tzinfo=timezone.utc)))
    return events


# ------------------------------------------------------------------
# commands


def cmd_list(args: argparse.Namespace) -> None:
    url, user, password = _load_creds()
    events = _list_events(url, user, password)
    from_dt = _iso_to_utc_dt(args.from_) if args.from_ else None
    to_dt = _iso_to_utc_dt(args.to) if args.to else None
    count = 0
    for ev in events:
        s = ev["start"]
        e = ev["end"]
        if s is None:
            continue
        s_utc = s.astimezone(timezone.utc)
        if from_dt and s_utc < from_dt:
            continue
        if to_dt and s_utc > to_dt:
            continue
        loc = f" ({ev['location']})" if ev.get("location") else ""
        print(f"{s.isoformat()}  {ev['summary']}{loc}")
        count += 1
    print(f"Events: {count}")


def cmd_today(args: argparse.Namespace) -> None:
    url, user, password = _load_creds()
    events = _list_events(url, user, password)
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30))).date()
    count = 0
    for ev in events:
        s = ev["start"]
        if s is None:
            continue
        local = s.astimezone(timezone(timedelta(hours=5, minutes=30)))
        if local.date() == today:
            loc = f" ({ev['location']})" if ev.get("location") else ""
            print(f"{local.strftime('%H:%M')}  {ev['summary']}{loc}")
            count += 1
    print(f"Today: {count}")


def cmd_upcoming(args: argparse.Namespace) -> None:
    url, user, password = _load_creds()
    events = _list_events(url, user, password)
    now = datetime.now(timezone.utc)
    days = args.days or 7
    horizon = now + timedelta(days=days)
    count = 0
    for ev in events:
        s = ev["start"]
        if s is None:
            continue
        s_utc = s.astimezone(timezone.utc)
        if s_utc >= now and s_utc <= horizon:
            loc = f" ({ev['location']})" if ev.get("location") else ""
            print(f"{s_utc.strftime('%Y-%m-%d %H:%M')}  {ev['summary']}{loc}")
            count += 1
    print(f"Upcoming: {count}")


def cmd_create(args: argparse.Namespace) -> None:
    url, user, password = _load_creds()
    uid = args.uid or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.urandom(4).hex()}@agentkit"
    ics = _build_ics(args.summary, uid, args.start, args.end, args.location)
    event_url = url + uid + ".ics"
    _request(event_url, "PUT", user=user, password=password,
             body=ics.encode("utf-8"), headers={"Content-Type": "text/calendar; charset=utf-8"})
    print(f"Created {args.summary} ({uid})")


def cmd_update(args: argparse.Namespace) -> None:
    url, user, password = _load_creds()
    event_url = url + args.uid + ".ics"
    try:
        old = _request(event_url, "GET", user=user, password=password).decode("utf-8", "replace")
    except CaldavError as exc:
        raise CaldavError(f"event not found: {args.uid}") from exc
    parsed = _parse_ics(old)
    summary = args.summary if args.summary is not None else (parsed.get("summary") or "")
    start = args.start if args.start is not None else (parsed.get("dtstart") or "")
    end = args.end if args.end is not None else (parsed.get("dtend") or "")
    loc = args.location if args.location is not None else (parsed.get("location") or "")
    if not summary or not start or not end:
        raise CaldavError("cannot update: missing summary/start/end after merge")
    ics = _build_ics(summary, args.uid, str(start), str(end), loc or None)
    _request(event_url, "PUT", user=user, password=password,
             body=ics.encode("utf-8"), headers={"Content-Type": "text/calendar; charset=utf-8"})
    print(f"Updated {args.uid}")


def cmd_delete(args: argparse.Namespace) -> None:
    url, user, password = _load_creds()
    event_url = url + args.uid + ".ics"
    _request(event_url, "DELETE", user=user, password=password)
    print(f"Deleted {args.uid}")


def cmd_freebusy(args: argparse.Namespace) -> None:
    url, user, password = _load_creds()
    events = _list_events(url, user, password)
    start = _iso_to_utc_dt(args.from_)
    end = _iso_to_utc_dt(args.to)
    if start is None or end is None or end <= start:
        raise CaldavError("invalid window")
    conflicts = []
    for ev in events:
        s = ev["start"]
        e = ev["end"]
        if s is None or e is None:
            continue
        s_utc = s.astimezone(timezone.utc)
        e_utc = e.astimezone(timezone.utc)
        if s_utc < end and e_utc > start:
            conflicts.append({"start": s_utc.isoformat(), "end": e_utc.isoformat(), "summary": ev["summary"]})
    print(f"free: {'yes' if not conflicts else 'no'}")
    for c in conflicts:
        print(f"  {c['start']} – {c['end']}  {c['summary']}")


# ------------------------------------------------------------------
# CLI

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="caldav.py",
        description="Caldav calendar operations (Nextcloud / standard CalDAV).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="List events in a date range.")
    p.add_argument("--from", dest="from_", metavar="ISO", help="Start (YYYY-MM-DD or ISO).")
    p.add_argument("--to", metavar="ISO", help="End (YYYY-MM-DD or ISO).")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("today", help="List today's events.")
    p.set_defaults(fn=cmd_today)

    p = sub.add_parser("upcoming", help="List upcoming events.")
    p.add_argument("--days", type=int, default=7, help="Horizon in days (default 7).")
    p.set_defaults(fn=cmd_upcoming)

    p = sub.add_parser("create", help="Create an event.")
    p.add_argument("--summary", required=True)
    p.add_argument("--start", required=True, help="ISO datetime, e.g. 2026-10-30T14:00:00+05:30")
    p.add_argument("--end", required=True)
    p.add_argument("--location", default=None)
    p.add_argument("--uid", default=None, help="Optional UID; auto-generated if omitted.")
    p.set_defaults(fn=cmd_create)

    p = sub.add_parser("update", help="Update an event by UID.")
    p.add_argument("--uid", required=True)
    p.add_argument("--summary")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--location")
    p.set_defaults(fn=cmd_update)

    p = sub.add_parser("delete", help="Delete an event by UID.")
    p.add_argument("--uid", required=True)
    p.set_defaults(fn=cmd_delete)

    p = sub.add_parser("freebusy", help="Check free/busy in a window.")
    p.add_argument("--from", dest="from_", required=True)
    p.add_argument("--to", required=True)
    p.set_defaults(fn=cmd_freebusy)

    return ap


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.fn(args)
    except CaldavError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
