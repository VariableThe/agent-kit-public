#!/usr/bin/env python3
"""Persistent, per-thread todo list for agent threads.

One list per scope (a T3 thread id, a named scope, or the working
directory). The list lives on disk, so an interrupted thread, a stopped
subagent, or a later session can pick the work up again from it.

Standard library only.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("pending", "in_progress", "blocked", "done")
OPEN_STATUSES = ("pending", "in_progress", "blocked")
SCHEMA_VERSION = 1


class TodoError(Exception):
    """User-facing failure: printed as `Error: ...` with exit code 1."""


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_root() -> Path:
    env = os.environ.get("AGENT_KIT_TODO_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "agent-kit" / "thread-todo"


def slug_for(scope: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", scope).strip("-") or "scope"
    base = base[-40:].lstrip("-")
    digest = hashlib.sha1(scope.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.lists_dir = root / "lists"
        self.index_path = root / "index.json"
        self._lock_path = root / ".lock"

    def ensure_dirs(self) -> None:
        self.lists_dir.mkdir(parents=True, exist_ok=True)

    # -- locking ----------------------------------------------------------
    def _acquire(self):
        """Best-effort exclusive lock; no-op where fcntl is unavailable."""
        try:
            import fcntl
        except ImportError:  # pragma: no cover - non-POSIX
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        handle = open(self._lock_path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:  # pragma: no cover - lock unsupported
            if exc.errno not in (errno.EINVAL, errno.ENOLCK, errno.EOPNOTSUPP):
                handle.close()
                raise
        return handle

    @staticmethod
    def _release(handle) -> None:
        if handle is None:
            return
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:  # pragma: no cover - best effort
            pass
        handle.close()

    # -- index ------------------------------------------------------------
    def read_index(self) -> dict:
        if not self.index_path.exists():
            return {"version": SCHEMA_VERSION, "scopes": {}, "threads": {}, "cwds": {}}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": SCHEMA_VERSION, "scopes": {}, "threads": {}, "cwds": {}}
        data.setdefault("scopes", {})
        data.setdefault("threads", {})
        data.setdefault("cwds", {})
        return data

    def write_index(self, index: dict) -> None:
        write_json(self.index_path, index)

    # -- lists ------------------------------------------------------------
    def list_path(self, slug: str) -> Path:
        return self.lists_dir / f"{slug}.json"

    def read_list(self, slug: str) -> dict | None:
        path = self.list_path(slug)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TodoError(f"cannot read list {slug}: {exc}") from exc

    def write_list(self, doc: dict) -> None:
        doc["updatedAt"] = now_iso()
        write_json(self.list_path(doc["slug"]), doc)

    def all_lists(self) -> list[dict]:
        if not self.lists_dir.exists():
            return []
        out = []
        for path in sorted(self.lists_dir.glob("*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return out


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# scope resolution
# --------------------------------------------------------------------------


def current_thread_id() -> str | None:
    for key in ("AGENT_KIT_THREAD_ID", "T3_THREAD_ID", "THREAD_ID", "PI_SESSION_ID"):
        value = os.environ.get(key)
        if value:
            return value
    return None


def current_cwd() -> str:
    return str(Path.cwd().resolve())


def resolve_scope(args) -> str:
    if args.scope:
        return args.scope
    if args.thread:
        return f"thread:{args.thread}"
    env_scope = os.environ.get("AGENT_KIT_TODO_SCOPE")
    if env_scope:
        return env_scope
    return f"cwd:{current_cwd()}"


def find_slug(store: Store, scope: str, index: dict) -> str | None:
    slug = index.get("scopes", {}).get(scope)
    if slug:
        return slug
    if scope.startswith("thread:"):
        slug = index.get("threads", {}).get(scope[len("thread:") :])
        if slug:
            return slug
    if scope.startswith("cwd:"):
        slug = index.get("cwds", {}).get(scope[len("cwd:") :])
        if slug:
            return slug
    return None


def load_list(store: Store, scope: str, index: dict, create: bool) -> dict:
    slug = find_slug(store, scope, index)
    if slug:
        doc = store.read_list(slug)
        if doc:
            return doc
    if not create:
        raise TodoError(
            f"no todo list for scope {scope!r} "
            f"(run `init`, or `add` to create it, or `lists` to see known lists)"
        )
    now = now_iso()
    doc = {
        "version": SCHEMA_VERSION,
        "slug": slug_for(scope),
        "scope": scope,
        "title": "",
        "status": "active",
        "createdAt": now,
        "updatedAt": now,
        "closedAt": None,
        "closeSummary": None,
        "sessions": [],
        "items": [],
        "events": [],
    }
    return doc


def register(store: Store, index: dict, doc: dict, scope: str) -> None:
    index.setdefault("scopes", {})[scope] = doc["slug"]
    if scope.startswith("thread:"):
        index.setdefault("threads", {})[scope[len("thread:") :]] = doc["slug"]
    if scope.startswith("cwd:"):
        index.setdefault("cwds", {})[scope[len("cwd:") :]] = doc["slug"]
    thread_id = current_thread_id()
    if thread_id and thread_id not in doc.get("sessions", []):
        doc.setdefault("sessions", []).append(thread_id)
        index.setdefault("threads", {})[thread_id] = doc["slug"]
    store.write_index(index)


def log_event(doc: dict, event: str, **fields) -> None:
    entry = {"at": now_iso(), "event": event}
    entry.update(fields)
    doc.setdefault("events", []).append(entry)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def find_item(doc: dict, item_id: int) -> dict:
    for item in doc.get("items", []):
        if item.get("id") == item_id:
            return item
    raise TodoError(f"no item {item_id} in list {doc.get('slug')}")


def next_id(doc: dict) -> int:
    ids = [item.get("id", 0) for item in doc.get("items", [])]
    return (max(ids) + 1) if ids else 1


def item_label(item: dict) -> str:
    return f"[{item['id']}] {item.get('status', 'pending')}"


def require_open(doc: dict) -> None:
    if doc.get("status") == "closed":
        raise TodoError(f"list {doc.get('slug')} is closed (run `reopen` first)")


def parse_notes(args) -> list[str]:
    notes = list(getattr(args, "note", None) or [])
    return [n for n in notes if n]


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_init(store: Store, scope: str, index: dict, args) -> int:
    existed = find_slug(store, scope, index) is not None
    doc = load_list(store, scope, index, create=True)
    if args.title:
        doc["title"] = args.title
    if not existed:
        log_event(doc, "init", scope=scope)
        store.write_list(doc)
    register(store, index, doc, scope)
    state = "exists" if existed else "created"
    print(f"List: {doc['slug']} ({state}, scope={scope})")
    return 0


def cmd_add(store: Store, scope: str, index: dict, args) -> int:
    doc = load_list(store, scope, index, create=True)
    require_open(doc)
    item_id = next_id(doc)
    for dep in args.depends_on or []:
        if dep == item_id:
            raise TodoError(f"item {item_id} cannot depend on itself")
        find_item(doc, dep)
    item = {
        "id": item_id,
        "text": args.text,
        "status": args.status,
        "notes": parse_notes(args),
        "dependsOn": list(args.depends_on or []),
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "doneAt": None,
    }
    doc.setdefault("items", []).append(item)
    log_event(doc, "add", id=item["id"], text=item["text"], status=item["status"])
    store.write_list(doc)
    register(store, index, doc, scope)
    print(f"Added: {item['id']}")
    return 0


def cmd_list(store: Store, scope: str, index: dict, args) -> int:
    doc = load_list(store, scope, index, create=False)
    items = doc.get("items", [])
    if args.status:
        items = [i for i in items if i.get("status") == args.status]
    elif not args.include_done:
        items = [i for i in items if i.get("status") != "done"]
    if args.json:
        print(json.dumps({"list": doc, "items": items}, ensure_ascii=False))
        return 0
    if not items:
        print("Items: 0")
        return 0
    for item in items:
        line = f"{item_label(item)} {item.get('text', '')}"
        if item.get("dependsOn"):
            line += f" (after {','.join(str(d) for d in item['dependsOn'])})"
        print(line)
        for note in item.get("notes", []):
            print(f"      note: {note}")
    print(f"Items: {len(items)}")
    return 0


def cmd_show(store: Store, scope: str, index: dict, args) -> int:
    doc = load_list(store, scope, index, create=False)
    item = find_item(doc, args.id)
    if args.json:
        print(json.dumps({"list": doc, "item": item}, ensure_ascii=False))
        return 0
    print(f"{item_label(item)} {item.get('text', '')}")
    print(f"  created: {item.get('createdAt')}  updated: {item.get('updatedAt')}")
    if item.get("dependsOn"):
        print(f"  depends on: {', '.join(str(d) for d in item['dependsOn'])}")
    for note in item.get("notes", []):
        print(f"  note: {note}")
    return 0


def cmd_note(store: Store, scope: str, index: dict, args) -> int:
    doc = load_list(store, scope, index, create=False)
    require_open(doc)
    item = find_item(doc, args.id)
    item.setdefault("notes", []).append(args.text)
    item["updatedAt"] = now_iso()
    log_event(doc, "note", id=item["id"], text=args.text)
    store.write_list(doc)
    register(store, index, doc, scope)
    print(f"Noted: {item['id']}")
    return 0


def cmd_status(store: Store, scope: str, index: dict, args, status: str) -> int:
    doc = load_list(store, scope, index, create=False)
    require_open(doc)
    item = find_item(doc, args.id)
    previous = item.get("status")
    item["status"] = status
    item["updatedAt"] = now_iso()
    if status == "done":
        item["doneAt"] = now_iso()
    else:
        item["doneAt"] = None
    extra = getattr(args, "note", None)
    if extra:
        item.setdefault("notes", []).append(extra)
    log_event(doc, "status", id=item["id"], **{"from": previous, "to": status})
    store.write_list(doc)
    register(store, index, doc, scope)
    print(f"{item['id']}: {previous} -> {status}")
    return 0


def cmd_remove(store: Store, scope: str, index: dict, args) -> int:
    doc = load_list(store, scope, index, create=False)
    require_open(doc)
    item = find_item(doc, args.id)
    if not args.yes:
        raise TodoError(f"remove {args.id} is destructive; re-run with --yes")
    doc["items"] = [i for i in doc["items"] if i.get("id") != args.id]
    log_event(doc, "remove", id=args.id, text=item.get("text", ""))
    store.write_list(doc)
    register(store, index, doc, scope)
    print(f"Removed: {args.id}")
    return 0


def cmd_close(store: Store, scope: str, index: dict, args) -> int:
    doc = load_list(store, scope, index, create=False)
    doc["status"] = "closed"
    doc["closedAt"] = now_iso()
    doc["closeSummary"] = args.summary
    if args.summary:
        doc.setdefault("items", [])
        log_event(doc, "close", summary=args.summary)
    else:
        log_event(doc, "close")
    store.write_list(doc)
    register(store, index, doc, scope)
    print(f"Closed: {doc['slug']}")
    return 0


def cmd_reopen(store: Store, scope: str, index: dict, args) -> int:
    doc = load_list(store, scope, index, create=False)
    doc["status"] = "active"
    doc["closedAt"] = None
    doc["closeSummary"] = None
    log_event(doc, "reopen", reason=args.reason)
    store.write_list(doc)
    register(store, index, doc, scope)
    print(f"Reopened: {doc['slug']}")
    return 0


def cmd_log(store: Store, scope: str, index: dict, args) -> int:
    doc = load_list(store, scope, index, create=False)
    events = doc.get("events", [])
    if args.limit:
        events = events[-args.limit :]
    if args.json:
        print(json.dumps(events, ensure_ascii=False))
        return 0
    for entry in events:
        bits = [entry.get("at", ""), entry.get("event", "")]
        for key in ("id", "text", "summary", "from", "to", "reason"):
            if entry.get(key) is not None:
                bits.append(f"{key}={entry[key]}")
        print(" ".join(str(b) for b in bits))
    print(f"Events: {len(events)}")
    return 0


def cmd_lists(store: Store, scope: str, index: dict, args) -> int:
    docs = store.all_lists()
    if args.json:
        print(json.dumps(docs, ensure_ascii=False))
        return 0
    if not docs:
        print("Lists: 0")
        return 0
    for doc in docs:
        open_items = [i for i in doc.get("items", []) if i.get("status") in OPEN_STATUSES]
        title = doc.get("title") or "-"
        print(
            f"{doc.get('slug')}  scope={doc.get('scope')}  status={doc.get('status')}  "
            f"open={len(open_items)}  updated={doc.get('updatedAt')}  title={title}"
        )
    print(f"Lists: {len(docs)}")
    return 0


def cmd_resume(store: Store, scope: str, index: dict, args) -> int:
    doc = load_list(store, scope, index, create=False)
    items = doc.get("items", [])
    done_ids = {i["id"] for i in items if i.get("status") == "done"}
    ready = [
        i
        for i in items
        if i.get("status") in OPEN_STATUSES
        and all(dep in done_ids for dep in i.get("dependsOn", []))
    ]
    next_item = None
    for candidate in items:
        if candidate.get("status") == "in_progress":
            next_item = candidate
            break
    if next_item is None and ready:
        next_item = ready[0]

    if args.json:
        print(
            json.dumps(
                {
                    "list": doc,
                    "open": [i for i in items if i.get("status") in OPEN_STATUSES],
                    "done": [i for i in items if i.get("status") == "done"],
                    "next": next_item,
                },
                ensure_ascii=False,
            )
        )
        return 0

    print(f"List: {doc.get('slug')}  scope={doc.get('scope')}  status={doc.get('status')}")
    if doc.get("title"):
        print(f"Title: {doc['title']}")
    if doc.get("status") == "closed":
        print("This list is CLOSED. Reopen it before adding work.")
        if doc.get("closeSummary"):
            print(f"Closed with: {doc['closeSummary']}")
    open_items = [i for i in items if i.get("status") in OPEN_STATUSES]
    done_items = [i for i in items if i.get("status") == "done"]
    print(f"Open: {len(open_items)}  Done: {len(done_items)}")
    for item in open_items:
        marker = ">>" if next_item and item["id"] == next_item["id"] else "  "
        print(f"{marker} {item_label(item)} {item.get('text', '')}")
        for note in item.get("notes", []):
            print(f"       note: {note}")
    if next_item:
        print(f"Next: {next_item['id']} {next_item.get('text', '')}")
    else:
        print("Next: nothing open")
    for event in doc.get("events", [])[-5:]:
        detail = event.get("text") or event.get("summary") or event.get("reason") or ""
        target = f" #{event['id']}" if event.get("id") is not None else ""
        print(f"  log: {event.get('at')} {event.get('event')}{target} {detail}".rstrip())
    return 0


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thread_todo.py",
        description="Persistent per-thread todo list that survives interruptions.",
    )
    parser.add_argument("--scope", help="explicit scope (shared by parent and subagents)")
    parser.add_argument("--thread", help="thread id scope (per-thread isolation)")
    parser.add_argument("--dir", help="storage directory (default: ~/.config/agent-kit/thread-todo)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create the list for this scope")
    p.add_argument("--title", help="list title")

    p = sub.add_parser("add", help="add an item")
    p.add_argument("text", help="what to do")
    p.add_argument("--note", action="append", help="note attached to the item (repeatable)")
    p.add_argument("--depends-on", type=int, action="append", help="item id that must finish first")
    p.add_argument("--status", choices=STATUSES, default="pending")

    p = sub.add_parser("list", help="list items")
    p.add_argument("--status", choices=STATUSES, help="only items with this status")
    p.add_argument("--include-done", action="store_true", help="also show completed items")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("show", help="show one item")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("note", help="attach a note to an item")
    p.add_argument("id", type=int)
    p.add_argument("text")

    p = sub.add_parser("start", help="mark an item in progress")
    p.add_argument("id", type=int)
    p.add_argument("--note")

    p = sub.add_parser("block", help="mark an item blocked")
    p.add_argument("id", type=int)
    p.add_argument("--note", help="why it is blocked")

    p = sub.add_parser("done", help="mark an item complete")
    p.add_argument("id", type=int)
    p.add_argument("--note")

    p = sub.add_parser("undo", help="move an item back to pending")
    p.add_argument("id", type=int)
    p.add_argument("--note")

    p = sub.add_parser("remove", help="delete an item (destructive)")
    p.add_argument("id", type=int)
    p.add_argument("--yes", action="store_true", help="confirm the deletion")

    p = sub.add_parser("resume", help="print the state to continue from")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("close", help="close the list (thread finished)")
    p.add_argument("--summary", help="one-line outcome summary")

    p = sub.add_parser("reopen", help="reopen a closed list")
    p.add_argument("--reason", help="why it is reopened")

    p = sub.add_parser("log", help="show the event log")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("lists", help="list every known list")
    p.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    store = Store(Path(args.dir).expanduser() if args.dir else default_root())
    scope = resolve_scope(args)
    store.ensure_dirs()

    handle = store._acquire()
    try:
        index = store.read_index()
        if args.command == "init":
            return cmd_init(store, scope, index, args)
        if args.command == "add":
            return cmd_add(store, scope, index, args)
        if args.command == "list":
            return cmd_list(store, scope, index, args)
        if args.command == "show":
            return cmd_show(store, scope, index, args)
        if args.command == "note":
            return cmd_note(store, scope, index, args)
        if args.command == "start":
            return cmd_status(store, scope, index, args, "in_progress")
        if args.command == "block":
            return cmd_status(store, scope, index, args, "blocked")
        if args.command == "done":
            return cmd_status(store, scope, index, args, "done")
        if args.command == "undo":
            return cmd_status(store, scope, index, args, "pending")
        if args.command == "remove":
            return cmd_remove(store, scope, index, args)
        if args.command == "resume":
            return cmd_resume(store, scope, index, args)
        if args.command == "close":
            return cmd_close(store, scope, index, args)
        if args.command == "reopen":
            return cmd_reopen(store, scope, index, args)
        if args.command == "log":
            return cmd_log(store, scope, index, args)
        if args.command == "lists":
            return cmd_lists(store, scope, index, args)
        raise TodoError(f"unknown command {args.command}")
    finally:
        store._release(handle)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except TodoError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except BrokenPipeError:  # pragma: no cover
        sys.exit(0)
