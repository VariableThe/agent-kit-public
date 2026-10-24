#!/usr/bin/env python3
"""Tests for skills/thread-todo/scripts/thread_todo.py.

Run: python3 skills/thread-todo/test/thread_todo_test.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "thread_todo.py"


class TodoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def run_cli(self, *args, expect_ok=True, env=None):
        cmd = [sys.executable, str(SCRIPT), "--dir", self.dir, *args]
        merged = dict(os.environ)
        merged.pop("AGENT_KIT_TODO_SCOPE", None)
        merged.pop("AGENT_KIT_THREAD_ID", None)
        merged.pop("T3_THREAD_ID", None)
        merged.pop("THREAD_ID", None)
        merged.pop("PI_SESSION_ID", None)
        if env:
            merged.update(env)
        proc = subprocess.run(cmd, capture_output=True, text=True, env=merged)
        if expect_ok and proc.returncode != 0:
            self.fail(f"expected success, got {proc.returncode}: {proc.stderr}")
        if not expect_ok and proc.returncode == 0:
            self.fail(f"expected failure, got success: {proc.stdout}")
        return proc

    def test_help(self):
        proc = self.run_cli("--help")
        self.assertIn("thread_todo.py", proc.stdout)

    def test_add_and_list(self):
        out = self.run_cli("--scope", "s1", "add", "write the parser").stdout
        self.assertIn("Added: 1", out)
        out = self.run_cli("--scope", "s1", "list").stdout
        self.assertIn("write the parser", out)
        self.assertIn("Items: 1", out)

    def test_scopes_are_isolated(self):
        self.run_cli("--scope", "a", "add", "task a")
        self.run_cli("--scope", "b", "add", "task b")
        out = self.run_cli("--scope", "a", "list").stdout
        self.assertIn("task a", out)
        self.assertNotIn("task b", out)

    def test_thread_scope_is_per_thread(self):
        self.run_cli("--thread", "thread-1", "add", "one")
        self.run_cli("--thread", "thread-2", "add", "two")
        out = self.run_cli("--thread", "thread-1", "list").stdout
        self.assertIn("one", out)
        self.assertNotIn("two", out)

    def test_status_transitions(self):
        self.run_cli("--scope", "s", "add", "task")
        self.run_cli("--scope", "s", "start", "1")
        out = self.run_cli("--scope", "s", "resume").stdout
        self.assertIn("Next: 1", out)
        self.assertIn(">>", out)
        self.run_cli("--scope", "s", "done", "1")
        out = self.run_cli("--scope", "s", "resume").stdout
        self.assertIn("Open: 0  Done: 1", out)
        self.assertIn("Next: nothing open", out)

    def test_resume_prefers_unblocked_item(self):
        self.run_cli("--scope", "s", "add", "first")
        self.run_cli("--scope", "s", "add", "second", "--depends-on", "1")
        out = self.run_cli("--scope", "s", "resume").stdout
        self.assertIn("Next: 1", out)
        self.run_cli("--scope", "s", "done", "1")
        out = self.run_cli("--scope", "s", "resume").stdout
        self.assertIn("Next: 2", out)

    def test_note_and_show(self):
        self.run_cli("--scope", "s", "add", "task")
        self.run_cli("--scope", "s", "note", "1", "context for later")
        out = self.run_cli("--scope", "s", "show", "1").stdout
        self.assertIn("context for later", out)
        out = self.run_cli("--scope", "s", "list").stdout
        self.assertIn("note: context for later", out)

    def test_state_survives_new_process_and_env_change(self):
        self.run_cli("--thread", "T", "add", "survives")
        out = self.run_cli("--thread", "T", "resume", env={"PI_SESSION_ID": "other"}).stdout
        self.assertIn("survives", out)

    def test_cwd_scope_resumes_across_sessions(self):
        cwd_scope = f"cwd:{os.getcwd()}"
        self.run_cli("--scope", cwd_scope, "add", "persisted in cwd")
        out = self.run_cli("--scope", cwd_scope, "resume").stdout
        self.assertIn("persisted in cwd", out)

    def test_close_blocks_writes_and_reopen_allows(self):
        self.run_cli("--scope", "s", "add", "task")
        self.run_cli("--scope", "s", "close", "--summary", "done for now")
        proc = self.run_cli("--scope", "s", "add", "more", expect_ok=False)
        self.assertIn("is closed", proc.stderr)
        self.run_cli("--scope", "s", "reopen", "--reason", "regression")
        self.run_cli("--scope", "s", "add", "more")

    def test_missing_list_is_an_error(self):
        proc = self.run_cli("--scope", "missing", "resume", expect_ok=False)
        self.assertIn("no todo list", proc.stderr)
        self.assertEqual(proc.returncode, 1)

    def test_remove_requires_yes(self):
        self.run_cli("--scope", "s", "add", "task")
        proc = self.run_cli("--scope", "s", "remove", "1", expect_ok=False)
        self.assertIn("--yes", proc.stderr)
        self.run_cli("--scope", "s", "remove", "1", "--yes")

    def test_unknown_item_is_an_error(self):
        self.run_cli("--scope", "s", "add", "task")
        proc = self.run_cli("--scope", "s", "done", "99", expect_ok=False)
        self.assertIn("no item 99", proc.stderr)

    def test_lists_command(self):
        self.run_cli("--scope", "s1", "add", "a")
        self.run_cli("--scope", "s2", "add", "b")
        out = self.run_cli("lists").stdout
        self.assertIn("Lists: 2", out)

    def test_json_output(self):
        self.run_cli("--scope", "s", "add", "task")
        out = self.run_cli("--scope", "s", "list", "--json").stdout
        payload = json.loads(out)
        self.assertEqual(payload["items"][0]["text"], "task")

    def test_log_records_events(self):
        self.run_cli("--scope", "s", "add", "task")
        self.run_cli("--scope", "s", "done", "1")
        out = self.run_cli("--scope", "s", "log").stdout
        self.assertIn("add", out)
        self.assertIn("status", out)

    def test_env_scope_override(self):
        self.run_cli("--scope", "envscope", "add", "from env")
        out = self.run_cli("list", env={"AGENT_KIT_TODO_SCOPE": "envscope"}).stdout
        self.assertIn("from env", out)

    def test_subagent_can_share_parent_scope(self):
        self.run_cli("--scope", "shared", "add", "parent task")
        self.run_cli("--scope", "shared", "add", "subagent task")
        out = self.run_cli("--scope", "shared", "resume").stdout
        self.assertIn("parent task", out)
        self.assertIn("subagent task", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
