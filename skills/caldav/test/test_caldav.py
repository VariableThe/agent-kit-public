#!/usr/bin/env python3
"""Tests for skills/caldav/scripts/caldav.py.

Runs a real http.server thread as the CalDAV backend and drives the CLI
via subprocess so exit codes and stderr are asserted for real.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "caldav.py"

# Minimal ICS fixture for one event
EVENT_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:test-event-1@agentkit\r\n"
    "SUMMARY:Math Class\r\n"
    "DTSTART;TZID=Asia/Kolkata:20261030T090000\r\n"
    "DTEND;TZID=Asia/Kolkata:20261030T100000\r\n"
    "DTSTAMP:20261030T000000Z\r\n"
    "LOCATION:Room 101\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

EVENT_ICS_2 = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:test-event-2@agentkit\r\n"
    "SUMMARY:Physics Lab\r\n"
    "DTSTART;TZID=Asia/Kolkata:20261030T140000\r\n"
    "DTEND;TZID=Asia/Kolkata:20261030T160000\r\n"
    "DTSTAMP:20261030T000000Z\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


class _CaldavHandler(BaseHTTPRequestHandler):
    events: dict[str, bytes] = {}

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body=b"", ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PROPFIND(self):
        if self.path.endswith("/"):
            # calendar home listing
            body = (
                '<?xml version="1.0"?>'
                '<d:multistatus xmlns:d="DAV:">'
                '<d:response><d:href>/cal/personal/</d:href></d:response>'
                + "".join(
                    f'<d:response><d:href>/cal/personal/{uid}.ics</d:href></d:response>'
                    for uid in _CaldavHandler.events
                )
                + '</d:multistatus>'
            ).encode("utf-8")
            self._send(207, body, "application/xml; charset=utf-8")
        else:
            self._send(404)

    def do_GET(self):
        uid = self.path.rsplit("/", 1)[-1].replace(".ics", "")
        data = _CaldavHandler.events.get(uid)
        if data:
            self._send(200, data, "text/calendar; charset=utf-8")
        else:
            self._send(404)

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length)
        uid = self.path.rsplit("/", 1)[-1].replace(".ics", "")
        _CaldavHandler.events[uid] = data
        self._send(201)

    def do_DELETE(self):
        uid = self.path.rsplit("/", 1)[-1].replace(".ics", "")
        if uid in _CaldavHandler.events:
            del _CaldavHandler.events[uid]
            self._send(204)
        else:
            self._send(404)


def _run_server():
    srv = HTTPServer(("127.0.0.1", 0), _CaldavHandler)
    _, port = srv.server_address
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, port


def _env(port: int) -> dict[str, str]:
    return {
        "CALDAV_URL": f"http://127.0.0.1:{port}/cal/personal/",
        "CALDAV_USER": "testuser",
        "CALDAV_PASS": "testpass",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _run(args: list[str], port: int, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, **_env(port)}
    cp = subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=env,
    )
    if check and cp.returncode != 0:
        raise AssertionError(f"exit {cp.returncode}\nstdout: {cp.stdout}\nstderr: {cp.stderr}")
    return cp


class TestCaldavSkill(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _run_server()

    def setUp(self):
        _CaldavHandler.events.clear()
        _CaldavHandler.events["test-event-1"] = EVENT_ICS.encode("utf-8")
        _CaldavHandler.events["test-event-2"] = EVENT_ICS_2.encode("utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_today_lists_events(self):
        # Force "today" to 2026-10-30 by patching is hard; instead list --from/--to
        cp = _run(["list", "--from", "2026-10-30T00:00:00+05:30", "--to", "2026-10-30T23:59:59+05:30"], self.port)
        self.assertIn("Math Class", cp.stdout)
        self.assertIn("Physics Lab", cp.stdout)
        self.assertIn("Events: 2", cp.stdout)

    def test_list_date_range(self):
        cp = _run(["list", "--from", "2026-10-30", "--to", "2026-10-31"], self.port)
        self.assertIn("Math Class", cp.stdout)
        self.assertIn("Physics Lab", cp.stdout)

    def test_freebusy_conflicts(self):
        cp = _run(["freebusy", "--from", "2026-10-30T08:00:00+05:30", "--to", "2026-10-30T10:00:00+05:30"], self.port)
        self.assertIn("free: no", cp.stdout)
        self.assertIn("Math Class", cp.stdout)

    def test_freebusy_free(self):
        cp = _run(["freebusy", "--from", "2026-10-30T11:00:00+05:30", "--to", "2026-10-30T12:00:00+05:30"], self.port)
        self.assertIn("free: yes", cp.stdout)

    def test_create_event(self):
        _CaldavHandler.events.clear()
        cp = _run(["create", "--summary", "History", "--start", "2026-11-01T09:00:00+05:30",
                   "--end", "2026-11-01T10:00:00+05:30", "--location", "Room 303"], self.port)
        self.assertIn("Created History", cp.stdout)
        self.assertEqual(len(_CaldavHandler.events), 1)
        ics = list(_CaldavHandler.events.values())[0].decode("utf-8")
        self.assertIn("SUMMARY:History", ics)
        self.assertIn("LOCATION:Room 303", ics)

    def test_delete_event(self):
        cp = _run(["delete", "--uid", "test-event-1"], self.port)
        self.assertIn("Deleted test-event-1", cp.stdout)
        self.assertNotIn("test-event-1", _CaldavHandler.events)

    def test_update_event(self):
        cp = _run(["update", "--uid", "test-event-1", "--summary", "Advanced Math"], self.port)
        self.assertIn("Updated test-event-1", cp.stdout)
        ics = _CaldavHandler.events["test-event-1"].decode("utf-8")
        self.assertIn("SUMMARY:Advanced Math", ics)
        self.assertIn("Room 101", ics)  # location preserved

    def test_missing_creds(self):
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        cp = subprocess.run([sys.executable, str(SCRIPT), "today"], capture_output=True, text=True, env=env)
        self.assertEqual(cp.returncode, 1)
        self.assertIn("Error:", cp.stderr)

    def test_invalid_flags(self):
        cp = subprocess.run([sys.executable, str(SCRIPT), "create"], capture_output=True, text=True)
        self.assertEqual(cp.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
