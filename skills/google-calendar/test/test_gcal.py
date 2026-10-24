"""Tests for skills/google-calendar/scripts/gcal.py.

Every test runs the real script as a subprocess against a real local
`http.server` mock of both the OAuth token endpoint and the Calendar
API (wired via GOOGLE_OAUTH_TOKEN_URL / GOOGLE_CALENDAR_API_BASE), with
HOME pointed at a temp directory. No live Google endpoint is contacted.
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
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "skills" / "google-calendar" / "scripts" / "gcal.py"

CLIENT_ID = "client-a"
CLIENT_SECRET = "secret-b"
REFRESH_TOKEN = "refresh-c"


def credential_file_text() -> str:
    """Exercise the parser: comments, export prefix, both quote styles."""
    return "\n".join([
        "# agent-kit Google Calendar credentials",
        "export GOOGLE_CLIENT_ID=\"{}\"".format(CLIENT_ID),
        "GOOGLE_CLIENT_SECRET='{}'".format(CLIENT_SECRET),
        "",
        "a line without an equals sign is skipped",
        "GOOGLE_REFRESH_TOKEN={}".format(REFRESH_TOKEN),
        "GOOGLE_CALENDAR_ID=primary",
        "",
    ])


class MockState:
    """What the mock server saw, plus switchable behaviours."""

    def __init__(self) -> None:
        self.token_mints = 0
        self.requests: list = []          # (method, path, query, body)
        self.calendar_requests = 0
        self.fail_first_calendar_with_401 = False
        self.calendar_status = 200
        self.calendar_body: dict = {"items": []}
        self.token_status = 200
        self.token_error = {"error": "invalid_grant"}


class MockHandler(BaseHTTPRequestHandler):
    state: MockState

    def log_message(self, *_args) -> None:  # silence test output
        pass

    def _record(self, body: dict | None) -> None:
        query = urllib.parse.urlparse(self.path).query
        self.state.requests.append((self.command, self.path, query, body))

    def _respond(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    def _serve_calendar(self, body: dict | None) -> None:
        self.state.calendar_requests += 1
        self._record(body)
        if (self.state.fail_first_calendar_with_401
                and self.state.calendar_requests == 1):
            self._respond(401, {"error": {"message": "Invalid Credentials"}})
            return
        self._respond(self.state.calendar_status, self.state.calendar_body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/token"):
            form = urllib.parse.parse_qs(
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                .decode("utf-8"))
            self.state.requests.append(("POST", "/token", "", form))
            self.state.token_mints += 1
            if self.state.token_status != 200:
                self._respond(self.state.token_status, self.state.token_error)
                return
            if (form.get("client_id") != [CLIENT_ID]
                    or form.get("client_secret") != [CLIENT_SECRET]
                    or form.get("refresh_token") != [REFRESH_TOKEN]
                    or form.get("grant_type") != ["refresh_token"]):
                self._respond(400, {"error": "invalid_client",
                                   "error_description": "Bad credentials"})
                return
            self._respond(200, {"access_token": "tok-{}".format(
                self.state.token_mints), "expires_in": 3600})
            return
        self._serve_calendar(self._read_body())

    def do_GET(self) -> None:  # noqa: N802
        self._serve_calendar(None)

    def do_PATCH(self) -> None:  # noqa: N802
        self._serve_calendar(self._read_body())

    def do_DELETE(self) -> None:  # noqa: N802
        self._serve_calendar(None)


class MockServer:
    """A thread serving one MockState until shut down."""

    def __init__(self) -> None:
        self.state = MockState()
        MockHandler.state = self.state
        self.server = HTTPServer(("127.0.0.1", 0), MockHandler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        host, port = self.server.server_address[:2]
        return "http://{}:{}".format(host, port)

    @property
    def token_url(self) -> str:
        return self.base + "/token"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class ScriptCase(unittest.TestCase):
    def run_script(self, home: str, *cli: str, extra_env: dict | None = None,
                   state: MockState | None = None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("GOOGLE_") and k != "AGENT_KIT_CREDENTIALS"}
        env["HOME"] = home
        if state is not None:
            env["GOOGLE_OAUTH_TOKEN_URL"] = self.mock.token_url
            env["GOOGLE_CALENDAR_API_BASE"] = self.mock.base
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *cli],
            capture_output=True, text=True, env=env, timeout=60)

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = self._tmp.name
        self.mock = MockServer()
        self.addCleanup(self.mock.stop)

    def write_credentials(self) -> None:
        config = Path(self.home, ".config", "agent-kit", "credentials")
        config.mkdir(parents=True)
        (config / "google-calendar.env").write_text(
            credential_file_text(), encoding="utf-8")


class CredentialsTest(ScriptCase):
    def test_credential_file_parsing(self) -> None:
        self.write_credentials()
        result = self.run_script(self.home, "auth-status", state=self.mock.state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Credentials: ok (calendar primary)", result.stdout)
        self.assertEqual(self.mock.state.token_mints, 1)

    def test_missing_credentials_message(self) -> None:
        empty_home = os.path.join(self.home, "empty")
        os.makedirs(empty_home)
        result = self.run_script(empty_home, "list")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            "Error: missing Google credentials (set GOOGLE_CLIENT_ID,"
            " GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN or a credential file)",
            result.stderr.strip())

    def test_invalid_grant_reported(self) -> None:
        self.write_credentials()
        self.mock.state.token_status = 400
        result = self.run_script(self.home, "auth-status", state=self.mock.state)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("Error: token refresh failed:"),
                        result.stderr)
        self.assertIn("invalid_grant", result.stderr)


class ListTest(ScriptCase):
    def test_list_events(self) -> None:
        self.write_credentials()
        self.mock.state.calendar_body = {"items": [
            {"summary": "Standup",
             "start": {"dateTime": "2026-03-04T09:30:00Z"},
             "end": {"dateTime": "2026-03-04T09:45:00Z"}},
            {"summary": "Review",
             "start": {"date": "2026-03-05"}, "end": {"date": "2026-03-06"}},
        ]}
        result = self.run_script(self.home, "list", "--from", "2026-03-04",
                                  "--to", "2026-03-11",
                                  state=self.mock.state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Standup", result.stdout)
        self.assertIn("Review", result.stdout)
        self.assertIn("Events: 2", result.stdout)
        queries = [r[2] for r in self.mock.state.requests if r[0] == "GET"]
        self.assertTrue(queries and "timeMin" in queries[0], self.mock.state.requests)

    def test_401_remints_once_and_retries(self) -> None:
        self.write_credentials()
        self.mock.state.fail_first_calendar_with_401 = True
        self.mock.state.calendar_body = {"items": []}
        result = self.run_script(self.home, "list", state=self.mock.state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Events: 0", result.stdout)
        self.assertEqual(self.mock.state.token_mints, 2,
                         "expected exactly one re-mint")

    def test_http_error_body_surfaced(self) -> None:
        self.write_credentials()
        self.mock.state.calendar_status = 404
        self.mock.state.calendar_body = {"error": {"message": "Not Found"}}
        result = self.run_script(self.home, "list", state=self.mock.state)
        self.assertEqual(result.returncode, 1)
        self.assertEqual("Error: HTTP 404 Not Found", result.stderr.strip())


class WriteTest(ScriptCase):
    def test_timed_create_payload(self) -> None:
        self.write_credentials()
        self.mock.state.calendar_body = {"id": "evt-1", "summary": "Dentist"}
        result = self.run_script(
            self.home, "create", "--summary", "Dentist",
            "--start", "2026-03-05T10:00", "--end", "2026-03-05T11:00",
            "--timezone", "UTC", state=self.mock.state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Created: evt-1", result.stdout)
        bodies = [r[3] for r in self.mock.state.requests if r[0] == "POST"
                  and "events" in r[1]]
        self.assertEqual(len(bodies), 1)
        body = bodies[0]
        self.assertEqual(body["start"]["dateTime"],
                         "2026-03-05T10:00:00+00:00")
        self.assertEqual(body["end"]["dateTime"], "2026-03-05T11:00:00+00:00")
        self.assertNotIn("date", body["start"])

    def test_all_day_create_payload(self) -> None:
        self.write_credentials()
        self.mock.state.calendar_body = {"id": "evt-2", "summary": "Offsite"}
        result = self.run_script(self.home, "create", "--summary", "Offsite",
                                "--all-day", "--date", "2026-03-09",
                                state=self.mock.state)
        self.assertEqual(result.returncode, 0, result.stderr)
        bodies = [r[3] for r in self.mock.state.requests
                  if r[0] == "POST" and "events" in r[1]]
        body = bodies[0]
        self.assertEqual(body["start"], {"date": "2026-03-09"})
        # All-day end is exclusive: a one-day event ends the next day.
        self.assertEqual(body["end"], {"date": "2026-03-10"})

    def test_update_sends_only_named_fields(self) -> None:
        self.write_credentials()
        result = self.run_script(self.home, "update", "evt-3",
                                 "--summary", "Renamed",
                                 state=self.mock.state)
        self.assertEqual(result.returncode, 0, result.stderr)
        patch = [r for r in self.mock.state.requests if r[0] == "PATCH"]
        self.assertEqual(len(patch), 1)
        self.assertEqual(patch[0][3], {"summary": "Renamed"})

    def test_delete_requires_yes(self) -> None:
        self.write_credentials()
        result = self.run_script(self.home, "delete", "evt-4")
        self.assertEqual(result.returncode, 1)
        self.assertEqual("Error: refusing to delete an event without --yes",
                         result.stderr.strip())
        self.assertFalse([r for r in self.mock.state.requests
                           if r[0] == "DELETE"],
                         "no request may be sent without --yes")

    def test_delete_with_yes(self) -> None:
        self.write_credentials()
        result = self.run_script(self.home, "delete", "evt-4", "--yes",
                                 state=self.mock.state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Deleted: evt-4", result.stdout)
        self.assertTrue([r for r in self.mock.state.requests
                         if r[0] == "DELETE"])


class FreebusyTest(ScriptCase):
    def test_freebusy_payload_and_output(self) -> None:
        self.write_credentials()
        self.mock.state.calendar_body = {"calendars": {"primary": {"busy": [
            {"start": "2026-03-04T09:30:00Z", "end": "2026-03-04T10:00:00Z"},
        ]}}}
        result = self.run_script(self.home, "freebusy", "--from", "2026-03-04",
                                 "--to", "2026-03-05",
                                 state=self.mock.state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Busy: 2026-03-04 09:30 - 2026-03-04 10:00",
                      result.stdout)
        self.assertIn("Freebusy: 1", result.stdout)
        body = [r[3] for r in self.mock.state.requests
                if r[0] == "POST" and r[1].endswith("/freeBusy")][0]
        self.assertEqual(body["items"], [{"id": "primary"}])
        self.assertTrue(body["timeMin"].startswith("2026-03-04T00:00:00"))


if __name__ == "__main__":
    unittest.main()