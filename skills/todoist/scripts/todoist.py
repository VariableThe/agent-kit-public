#!/usr/bin/env python3
"""Todoist task manager client (Todoist API v1).

One self-contained CLI: list projects, labels, sections and tasks, and
create, inspect, update, complete, reopen or delete a task.

Standard library only -- `python3 scripts/todoist.py --help` works with
zero setup. The API token comes from TODOIST_API_TOKEN or a credential
file; it is never printed and never written to disk.

The legacy base URL https://api.todoist.com/rest/v2 is retired and answers
HTTP 410 -- only /api/v1 is used here.
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API_BASE = "https://api.todoist.com/api/v1"
TOKEN_ENV = "TODOIST_API_TOKEN"
BASE_ENV = "TODOIST_API_BASE"
CREDENTIALS_ENV = "AGENT_KIT_CREDENTIALS"
USER_AGENT = "agent-kit-todoist/1.0"
TIMEOUT = 30

MISSING_TOKEN = "missing Todoist token (set TODOIST_API_TOKEN or a credential file)"

# The API's numeric priority runs 1..4 with 4 = most urgent, the inverse of
# the p1..p4 names clients show. The CLI takes the client spelling and
# translates it; a raw number is read as the client number and translated
# the same way.
PRIORITY_BY_NAME = {"p1": 4, "p2": 3, "p3": 2, "p4": 1}
PRIORITY_CHOICES = ["p1", "p2", "p3", "p4", "1", "2", "3", "4"]

# The API only serves completed tasks for a limited completion-date window.
COMPLETED_WINDOW_DAYS = 30

DUE_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
DUE_DATETIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$")
DUE_OFFSET_RE = re.compile(r"^\+(\d+)([dh])$")
EXPORT_RE = re.compile(r"^export\s+")


class TodoistError(Exception):
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
    paths.append(credentials / "todoist.env")
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


def resolve_token() -> str:
    token = resolve_setting(TOKEN_ENV)
    if not token:
        raise TodoistError(MISSING_TOKEN)
    return token


# --------------------------------------------------------------------------
# due dates


def parse_priority(value: str) -> int:
    """Map a client priority (p1..p4 or 1..4) to the API's 1..4 number."""
    key = value.strip().lower()
    if key in PRIORITY_BY_NAME:
        return PRIORITY_BY_NAME[key]
    if key in ("1", "2", "3", "4"):
        return PRIORITY_BY_NAME["p" + key]
    raise TodoistError(f"unknown priority: {value}")


def parse_due(spec: str) -> dict[str, str]:
    """Turn a --due spec into the API's due-date fields.

    Date-only values become `due_date` (YYYY-MM-DD, no time of day); values
    with a time become `due_datetime` (RFC 3339, UTC, e.g. 2025-03-04T09:30:00Z).
    """
    raw = spec.strip()
    lowered = raw.lower()
    now = datetime.now().astimezone()

    if lowered == "today":
        return {"due_date": now.date().isoformat()}
    if lowered == "tomorrow":
        return {"due_date": (now.date() + timedelta(days=1)).isoformat()}

    match = DUE_OFFSET_RE.match(lowered)
    if match:
        amount = int(match.group(1))
        if match.group(2) == "d":
            return {"due_date": (now.date() + timedelta(days=amount)).isoformat()}
        return {"due_datetime": utc_stamp(now + timedelta(hours=amount))}

    match = DUE_DATE_RE.match(raw)
    if match:
        try:
            day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            raise TodoistError(f"cannot parse due date: {spec}") from None
        return {"due_date": day.isoformat()}

    match = DUE_DATETIME_RE.match(raw)
    if match:
        try:
            when = datetime(
                int(match.group(1)), int(match.group(2)), int(match.group(3)),
                int(match.group(4)), int(match.group(5)),
                int(match.group(6) or 0),
            )
        except ValueError:
            raise TodoistError(f"cannot parse due date: {spec}") from None
        # A time means an instant: send it in UTC, the form the v1 API
        # documents for due_datetime ("2025-02-12T12:00:00Z").
        return {"due_datetime": utc_stamp(when)}

    raise TodoistError(f"cannot parse due date: {spec}")


def utc_stamp(moment: datetime) -> str:
    """RFC 3339 UTC stamp, e.g. 2025-03-04T09:30:00Z."""
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------
# HTTP


def api_base() -> str:
    return os.environ.get(BASE_ENV, API_BASE).rstrip("/")


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
        for key in ("error", "error_description", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                for nested_key in ("message", "error"):
                    nested = value.get(nested_key)
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()
    elif isinstance(payload, str) and payload.strip():
        return payload.strip()
    return text.splitlines()[0]


def api_request(token: str, method: str, path: str, *, query: dict | None = None,
                body: dict | None = None) -> object:
    """Call the API and return the decoded JSON payload (or None)."""
    url = api_base() + path
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    data = None
    headers = {
        "Authorization": "Bearer " + token,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        message = error_message(exc.read())
        raise TodoistError(f"HTTP {exc.code} {message}".rstrip()) from None
    except urllib.error.URLError as exc:
        raise TodoistError(f"request failed: {exc.reason}") from None
    except OSError as exc:
        raise TodoistError(f"request failed: {exc}") from None
    if not raw.strip():
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise TodoistError("invalid JSON in API response") from None


def envelope_items(payload: object) -> list:
    """Items of a v1 list response (`results`, or `items` for completed tasks)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def task_path(task_id: str, suffix: str = "") -> str:
    return "/tasks/" + urllib.parse.quote(task_id, safe="") + suffix


def one_line(text: object, limit: int = 72) -> str:
    flat = " ".join(str(text or "").split())
    if len(flat) > limit:
        flat = flat[: limit - 3] + "..."
    return flat


def print_items(items: list, label: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps(items, ensure_ascii=False))
    else:
        print(f"{label}: {len(items)}")


# --------------------------------------------------------------------------
# commands


def cmd_auth_status(token: str, args: argparse.Namespace) -> None:
    api_request(token, "GET", "/user")
    print("Credentials: ok")


def cmd_projects(token: str, args: argparse.Namespace) -> None:
    items = envelope_items(api_request(token, "GET", "/projects"))
    print_items(items, "Projects", args.json)


def cmd_labels(token: str, args: argparse.Namespace) -> None:
    items = envelope_items(api_request(token, "GET", "/labels"))
    print_items(items, "Labels", args.json)


def cmd_sections(token: str, args: argparse.Namespace) -> None:
    items = envelope_items(api_request(token, "GET", "/sections", query={"project_id": args.project}))
    print_items(items, "Sections", args.json)


def cmd_tasks(token: str, args: argparse.Namespace) -> None:
    if args.completed:
        now = datetime.now(timezone.utc)
        query: dict = {
            "since": utc_stamp(now - timedelta(days=COMPLETED_WINDOW_DAYS)),
            "until": utc_stamp(now),
        }
        if args.project:
            query["project_id"] = args.project
        if args.label:
            query["filter_query"] = " | ".join("@" + name for name in args.label)
        payload = api_request(token, "GET", "/tasks/completed/by_completion_date", query=query)
        items = envelope_items(payload)
    elif len(args.label) > 1:
        # GET /tasks takes a single label, so several labels go to the filter
        # endpoint. That endpoint cannot filter by project ID, so the project
        # is applied to the returned tasks afterwards.
        query = {"query": " | ".join("@" + name for name in args.label)}
        payload = api_request(token, "GET", "/tasks/filter", query=query)
        items = envelope_items(payload)
        if args.project:
            items = [item for item in items
                     if isinstance(item, dict) and item.get("project_id") == args.project]
    else:
        query = {}
        if args.project:
            query["project_id"] = args.project
        if args.label:
            query["label"] = args.label[0]
        payload = api_request(token, "GET", "/tasks", query=query)
        items = envelope_items(payload)
    print_items(items, "Tasks", args.json)


def cmd_add(token: str, args: argparse.Namespace) -> None:
    body: dict = {"content": args.content}
    if args.project:
        body["project_id"] = args.project
    if args.section:
        body["section_id"] = args.section
    if args.description is not None:
        body["description"] = args.description
    if args.label:
        body["labels"] = list(args.label)
    if args.priority:
        body["priority"] = parse_priority(args.priority)
    if args.due:
        body.update(parse_due(args.due))
    payload = api_request(token, "POST", "/tasks", body=body)
    task_id = payload.get("id", "") if isinstance(payload, dict) else ""
    print(f"Created: {task_id}")


def cmd_show(token: str, args: argparse.Namespace) -> None:
    payload = api_request(token, "GET", task_path(args.task_id))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    task_id = payload.get("id", args.task_id) if isinstance(payload, dict) else args.task_id
    content = payload.get("content", "") if isinstance(payload, dict) else ""
    print(f"Task: {task_id} - {one_line(content)}")


def cmd_update(token: str, args: argparse.Namespace) -> None:
    body: dict = {}
    if args.content is not None:
        body["content"] = args.content
    if args.description is not None:
        body["description"] = args.description
    if args.priority:
        body["priority"] = parse_priority(args.priority)
    if args.due:
        body.update(parse_due(args.due))

    move: dict = {}
    if args.project:
        move["project_id"] = args.project
    if args.section:
        move["section_id"] = args.section
    if not body and not move:
        raise TodoistError(
            "nothing to update (use --content/--due/--priority/--description/--project/--section)"
        )

    if body:
        api_request(token, "POST", task_path(args.task_id), body=body)
    if move:
        # The move endpoint takes the target fields on their own: a section
        # ID already identifies its project.
        api_request(token, "POST", task_path(args.task_id, "/move"), body=move)
    print(f"Updated: {args.task_id}")


def cmd_done(token: str, args: argparse.Namespace) -> None:
    api_request(token, "POST", task_path(args.task_id, "/close"))
    print(f"Completed: {args.task_id}")


def cmd_reopen(token: str, args: argparse.Namespace) -> None:
    api_request(token, "POST", task_path(args.task_id, "/reopen"))
    print(f"Reopened: {args.task_id}")


def cmd_delete(token: str, args: argparse.Namespace) -> None:
    api_request(token, "DELETE", task_path(args.task_id))
    print(f"Deleted: {args.task_id}")


# --------------------------------------------------------------------------
# CLI


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="todoist.py",
        description="Todoist task manager (projects, labels, sections, tasks).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("auth-status", help="Check that the stored token is accepted.")
    p.set_defaults(fn=cmd_auth_status)

    p = sub.add_parser("projects", help="List projects.")
    p.add_argument("--json", action="store_true", help="Print the raw JSON payload.")
    p.set_defaults(fn=cmd_projects)

    p = sub.add_parser("labels", help="List labels.")
    p.add_argument("--json", action="store_true", help="Print the raw JSON payload.")
    p.set_defaults(fn=cmd_labels)

    p = sub.add_parser("sections", help="List the sections of a project.")
    p.add_argument("--project", required=True, metavar="ID", help="Project ID.")
    p.add_argument("--json", action="store_true", help="Print the raw JSON payload.")
    p.set_defaults(fn=cmd_sections)

    p = sub.add_parser("tasks", help="List tasks (incomplete ones by default).")
    p.add_argument("--project", metavar="ID", help="Only tasks in this project.")
    p.add_argument("--label", action="append", metavar="NAME", default=[],
                   help="Only tasks with this label (repeatable).")
    p.add_argument("--completed", action="store_true",
                   help=f"List tasks completed in the last {COMPLETED_WINDOW_DAYS} days instead.")
    p.add_argument("--json", action="store_true", help="Print the raw JSON payload.")
    p.set_defaults(fn=cmd_tasks)

    p = sub.add_parser("add", help="Create a task.")
    p.add_argument("content", help="Task content.")
    p.add_argument("--project", metavar="ID", help="Project ID (default: Inbox).")
    p.add_argument("--section", metavar="ID", help="Section ID.")
    p.add_argument("--due", metavar="SPEC", help="Due date: today, tomorrow, YYYY-MM-DD, "
                                                 "YYYY-MM-DDTHH:MM, +3d, +2h.")
    p.add_argument("--priority", choices=PRIORITY_CHOICES, metavar="P",
                   help="Priority: p1 (urgent) .. p4 (normal), or the raw number 1..4.")
    p.add_argument("--label", action="append", metavar="NAME", default=[],
                   help="Label name (repeatable).")
    p.add_argument("--description", metavar="TEXT", help="Task description.")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("show", help="Show one task.")
    p.add_argument("task_id")
    p.add_argument("--json", action="store_true", help="Print the raw JSON payload.")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("update", help="Update a task.")
    p.add_argument("task_id")
    p.add_argument("--content", metavar="TEXT", help="New content.")
    p.add_argument("--due", metavar="SPEC", help="New due date (same forms as add).")
    p.add_argument("--priority", choices=PRIORITY_CHOICES, metavar="P",
                   help="New priority: p1 (urgent) .. p4 (normal), or the raw number 1..4.")
    p.add_argument("--description", metavar="TEXT", help="New description.")
    p.add_argument("--project", metavar="ID", help="Move the task to this project.")
    p.add_argument("--section", metavar="ID", help="Move the task to this section.")
    p.set_defaults(fn=cmd_update)

    p = sub.add_parser("done", help="Mark a task complete.")
    p.add_argument("task_id")
    p.set_defaults(fn=cmd_done)

    p = sub.add_parser("reopen", help="Mark a task incomplete again.")
    p.add_argument("task_id")
    p.set_defaults(fn=cmd_reopen)

    p = sub.add_parser("delete", help="Delete a task (needs --yes).")
    p.add_argument("task_id")
    p.add_argument("--yes", action="store_true", help="Confirm the deletion.")
    p.set_defaults(fn=cmd_delete)

    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        # The delete guard runs before credentials are touched: refusing to
        # delete must never depend on a token being present.
        if args.cmd == "delete" and not args.yes:
            raise TodoistError("refusing to delete a task without --yes")
        token = resolve_token()
        args.fn(token, args)
    except TodoistError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - one-line failure contract
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
