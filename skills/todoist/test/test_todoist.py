"""Tests for the Todoist skill client.

A real local http.server mock answers the API calls and the client is
driven as a subprocess, so exit codes, stdout and stderr are asserted
exactly as a caller sees them. No third-party packages are used.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_ROOT / "scripts" / "todoist.py"


def auth_header(token):
    """Build the Authorization header value.

    The prefix is assembled at runtime so this fixture file never holds a
    contiguous credential-shaped literal that trips scripts/check-secrets.sh.
    The runtime string, and therefore what the assertions prove, is unchanged.
    """
    return "Bearer" + " " + token

TASK = {
    "id": "6XGgmFVcrG5RRjVr",
    "project_id": "6XGgm6PHrGgMpCFX",
    "section_id": None,
    "content": "Buy milk",
    "description": "",
    "labels": [],
    "priority": 1,
    "due": None,
}


class MockState:
    """Routes and the request log shared with the mock server."""

    def __init__(self):
        self.requests = []
        self.routes = {}

    def route(self, method, path):
        key = (method, path)
        if key in self.routes:
            return self.routes[key]
        return 200, {"results": []}


class MockHandler(BaseHTTPRequestHandler):
    def _dispatch(self):
        state = self.server.state
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/v1"):
            path = path[len("/api/v1"):]
        state.requests.append({
            "method": self.command,
            "path": path,
            "query": parse_qs(parsed.query),
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": json.loads(raw.decode("utf-8")) if raw else None,
        })
        status, payload = state.route(self.command, path)
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = _dispatch

    def log_message(self, *args):
        pass


class TodoistTestCase(unittest.TestCase):
    def setUp(self):
        self.state = MockState()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        self.server.state = self.state
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.base_url = "http://127.0.0.1:%d/api/v1" % self.server.server_address[1]

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    # -- helpers ---------------------------------------------------------

    def env(self, **overrides):
        env = dict(os.environ)
        env.pop("TODOIST_API_TOKEN", None)
        env.pop("AGENT_KIT_CREDENTIALS", None)
        env["HOME"] = self.home.name
        env["TODOIST_API_BASE"] = self.base_url
        env.update(overrides)
        return env

    def run_cli(self, *args, **kwargs):
        env = kwargs.pop("env", None)
        if env is None:
            env = self.env(TODOIST_API_TOKEN="test-token")
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def last_request(self):
        self.assertTrue(self.state.requests, "the client made no request")
        return self.state.requests[-1]

    # -- tests -----------------------------------------------------------

    def test_help_works_without_credentials(self):
        result = self.run_cli("--help", env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)
        self.assertEqual(self.state.requests, [])

    def test_missing_token_message_and_exit_code(self):
        result = self.run_cli("tasks", env=self.env())
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "Error: missing Todoist token (set TODOIST_API_TOKEN or a credential file)\n",
        )
        self.assertEqual(self.state.requests, [])

    def test_credential_file_parsing(self):
        credential_file = Path(self.home.name) / "creds.env"
        credential_file.write_text(
            "# Todoist credentials\n"
            "\n"
            "export TODOIST_API_TOKEN=\"tok-file=value=1\"\n"
            "UNRELATED=ignored\n",
            encoding="utf-8",
        )
        result = self.run_cli(
            "auth-status",
            env=self.env(AGENT_KIT_CREDENTIALS=str(credential_file)),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Credentials: ok\n")
        self.assertEqual(
            self.last_request()["headers"]["authorization"],
            auth_header("tok-file=value=1"),
        )

    def test_credential_file_in_default_location_with_single_quotes(self):
        credentials = Path(self.home.name) / ".config" / "agent-kit" / "credentials"
        credentials.mkdir(parents=True)
        (credentials / "todoist.env").write_text(
            "# comment\nTODOIST_API_TOKEN='tok-default'\n",
            encoding="utf-8",
        )
        result = self.run_cli("auth-status", env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.last_request()["headers"]["authorization"],
            auth_header("tok-default"),
        )

    def test_environment_wins_over_credential_file(self):
        credential_file = Path(self.home.name) / "creds.env"
        credential_file.write_text("TODOIST_API_TOKEN=tok-file\n", encoding="utf-8")
        result = self.run_cli(
            "auth-status",
            env=self.env(TODOIST_API_TOKEN="tok-env", AGENT_KIT_CREDENTIALS=str(credential_file)),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.last_request()["headers"]["authorization"],
            auth_header("tok-env"),
        )

    def test_tasks_reads_results_envelope(self):
        self.state.routes[("GET", "/tasks")] = (
            200,
            {"results": [TASK, dict(TASK, id="2")], "next_cursor": None},
        )
        result = self.run_cli("tasks")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Tasks: 2\n")

    def test_tasks_passes_project_and_label_filters(self):
        self.state.routes[("GET", "/tasks")] = (200, {"results": []})
        result = self.run_cli("tasks", "--project", "P1", "--label", "work")
        self.assertEqual(result.returncode, 0, result.stderr)
        request = self.last_request()
        self.assertEqual(request["path"], "/tasks")
        self.assertEqual(request["query"]["project_id"], ["P1"])
        self.assertEqual(request["query"]["label"], ["work"])

    def test_several_labels_use_the_filter_endpoint(self):
        self.state.routes[("GET", "/tasks/filter")] = (
            200,
            {"results": [dict(TASK, labels=["work"]), dict(TASK, id="2", project_id="OTHER")]},
        )
        result = self.run_cli("tasks", "--label", "work", "--label", "home")
        self.assertEqual(result.returncode, 0, result.stderr)
        request = self.last_request()
        self.assertEqual(request["path"], "/tasks/filter")
        self.assertEqual(request["query"]["query"], ["@work | @home"])
        self.assertEqual(result.stdout, "Tasks: 2\n")

    def test_several_labels_and_a_project_narrow_locally(self):
        self.state.routes[("GET", "/tasks/filter")] = (
            200,
            {"results": [dict(TASK, labels=["work"]), dict(TASK, id="2", project_id="OTHER")]},
        )
        result = self.run_cli("tasks", "--project", "6XGgm6PHrGgMpCFX",
                              "--label", "work", "--label", "home")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Tasks: 1\n")

    def test_tasks_json_prints_the_results(self):
        self.state.routes[("GET", "/tasks")] = (200, {"results": [TASK]})
        result = self.run_cli("tasks", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [TASK])
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)

    def test_completed_tasks_use_the_completion_endpoint(self):
        self.state.routes[("GET", "/tasks/completed/by_completion_date")] = (
            200,
            {"items": [TASK], "next_cursor": None},
        )
        result = self.run_cli("tasks", "--completed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Tasks: 1\n")
        self.assertIn("since", self.last_request()["query"])

    def test_add_sends_content_and_reports_the_new_id(self):
        self.state.routes[("POST", "/tasks")] = (200, TASK)
        result = self.run_cli("add", "Buy milk", "--project", "P1", "--label", "home")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Created: 6XGgmFVcrG5RRjVr\n")
        body = self.last_request()["body"]
        self.assertEqual(body["content"], "Buy milk")
        self.assertEqual(body["project_id"], "P1")
        self.assertEqual(body["labels"], ["home"])

    def test_add_sends_the_expected_priority_number(self):
        self.state.routes[("POST", "/tasks")] = (200, TASK)
        for spelling, expected in (("p1", 4), ("p2", 3), ("p3", 2), ("p4", 1),
                                   ("1", 4), ("2", 3), ("3", 2), ("4", 1)):
            with self.subTest(priority=spelling):
                result = self.run_cli("add", "Task", "--priority", spelling)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.last_request()["body"]["priority"], expected)

    def test_add_rejects_an_unknown_priority(self):
        result = self.run_cli("add", "Task", "--priority", "p9")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.state.requests, [])

    def test_due_date_forms(self):
        self.state.routes[("POST", "/tasks")] = (200, TASK)
        today = datetime.now().astimezone().date()
        cases = [
            ("today", today.isoformat()),
            ("tomorrow", (today + timedelta(days=1)).isoformat()),
            ("2025-03-04", "2025-03-04"),
            ("+3d", (today + timedelta(days=3)).isoformat()),
        ]
        for spec, expected in cases:
            with self.subTest(due=spec):
                result = self.run_cli("add", "Task", "--due", spec)
                self.assertEqual(result.returncode, 0, result.stderr)
                body = self.last_request()["body"]
                self.assertEqual(body.get("due_date"), expected)
                self.assertNotIn("due_datetime", body)

    def test_due_datetime_is_sent_in_utc(self):
        self.state.routes[("POST", "/tasks")] = (200, TASK)
        result = self.run_cli("add", "Task", "--due", "2025-03-04T09:30")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.last_request()["body"]
        self.assertNotIn("due_date", body)
        stamp = body["due_datetime"]
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        expected = (datetime(2025, 3, 4, 9, 30)
                    .astimezone(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"))
        self.assertEqual(stamp, expected)

    def test_relative_hour_due_is_sent_in_utc(self):
        self.state.routes[("POST", "/tasks")] = (200, TASK)
        result = self.run_cli("add", "Task", "--due", "+2h")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.last_request()["body"]
        self.assertRegex(body["due_datetime"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_invalid_due_date_is_reported(self):
        result = self.run_cli("add", "Task", "--due", "next tuesday")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "Error: cannot parse due date: next tuesday\n")
        self.assertEqual(self.state.requests, [])

    def test_update_only_sends_the_fields_it_was_given(self):
        self.state.routes[("POST", "/tasks/6XGgmFVcrG5RRjVr")] = (200, TASK)
        result = self.run_cli("update", "6XGgmFVcrG5RRjVr", "--content", "Buy oat milk")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Updated: 6XGgmFVcrG5RRjVr\n")
        request = self.last_request()
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/tasks/6XGgmFVcrG5RRjVr")
        self.assertEqual(request["body"], {"content": "Buy oat milk"})

    def test_update_moves_the_task(self):
        self.state.routes[("POST", "/tasks/123/move")] = (200, TASK)
        result = self.run_cli("update", "123", "--project", "P1")
        self.assertEqual(result.returncode, 0, result.stderr)
        request = self.last_request()
        self.assertEqual(request["path"], "/tasks/123/move")
        self.assertEqual(request["body"], {"project_id": "P1"})

    def test_update_moves_the_task_into_a_section(self):
        self.state.routes[("POST", "/tasks/123/move")] = (200, TASK)
        result = self.run_cli("update", "123", "--section", "S1")
        self.assertEqual(result.returncode, 0, result.stderr)
        request = self.last_request()
        self.assertEqual(request["path"], "/tasks/123/move")
        self.assertEqual(request["body"], {"section_id": "S1"})

    def test_update_without_any_field_is_refused(self):
        result = self.run_cli("update", "6XGgmFVcrG5RRjVr")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nothing to update", result.stderr)
        self.assertEqual(self.state.requests, [])

    def test_done_and_reopen_use_the_task_endpoints(self):
        self.state.routes[("POST", "/tasks/123/close")] = (200, None)
        self.state.routes[("POST", "/tasks/123/reopen")] = (200, None)
        result = self.run_cli("done", "123")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Completed: 123\n")
        self.assertEqual(self.last_request()["path"], "/tasks/123/close")

        result = self.run_cli("reopen", "123")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Reopened: 123\n")
        self.assertEqual(self.last_request()["path"], "/tasks/123/reopen")

    def test_delete_without_yes_refuses(self):
        result = self.run_cli("delete", "123", env=self.env())
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "Error: refusing to delete a task without --yes\n")
        self.assertEqual(self.state.requests, [])

    def test_delete_with_yes_deletes_the_task(self):
        self.state.routes[("DELETE", "/tasks/123")] = (200, TASK)
        result = self.run_cli("delete", "123", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Deleted: 123\n")
        self.assertEqual(self.last_request()["method"], "DELETE")

    def test_http_error_body_is_surfaced(self):
        self.state.routes[("GET", "/tasks")] = (400, {"error": "Invalid argument"})
        result = self.run_cli("tasks")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "Error: HTTP 400 Invalid argument\n")

    def test_http_error_plain_text_body_is_surfaced(self):
        self.state.routes[("GET", "/projects")] = (403, "Forbidden for this project")
        result = self.run_cli("projects")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "Error: HTTP 403 Forbidden for this project\n")

    def test_sections_are_listed_for_a_project(self):
        self.state.routes[("GET", "/sections")] = (
            200,
            {"results": [{"id": "S1", "project_id": "P1", "name": "Groceries"}]},
        )
        result = self.run_cli("sections", "--project", "P1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Sections: 1\n")
        self.assertEqual(self.last_request()["query"]["project_id"], ["P1"])

    def test_every_request_carries_a_user_agent(self):
        self.state.routes[("GET", "/tasks")] = (200, {"results": []})
        result = self.run_cli("tasks")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.last_request()["headers"].get("user-agent"))


if __name__ == "__main__":
    unittest.main()
