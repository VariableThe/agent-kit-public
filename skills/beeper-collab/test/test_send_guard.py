"""Unit tests for the pre-send recipient gate (stdlib unittest, no network).

A numeric chatID is a local row id inside one Beeper instance; the same
number can name a different person in another transport. check_recipient is
the pure decision, cmd_send applies it before anything is transmitted.
"""

import contextlib
import importlib.util
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

_SCRIPT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts",
    "beeper_collab.py"))

_spec = importlib.util.spec_from_file_location("beeper_collab", _SCRIPT)
bc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bc)


class CheckRecipientTest(unittest.TestCase):
    def test_numeric_with_matching_title_is_allowed(self):
        res = bc.check_recipient("926", "Alex 1001", "Alex 1001")
        self.assertTrue(res["allowed"], res)
        self.assertEqual(res["chatID"], "926")
        self.assertEqual(res["title"], "Alex 1001")

    def test_numeric_with_wrong_title_is_refused_naming_both(self):
        res = bc.check_recipient("926", "Alex 1001", "a contact")
        self.assertFalse(res["allowed"])
        self.assertIn("Alex 1001", res["reason"])
        self.assertIn("a contact", res["reason"])
        self.assertIn("926", res["reason"])

    def test_numeric_without_expect_title_is_refused(self):
        res = bc.check_recipient("926", None, "Alex 1001")
        self.assertFalse(res["allowed"])
        self.assertIn("expect-title", res["reason"])
        self.assertIn("local", res["reason"])

    def test_numeric_with_unresolvable_title_is_refused(self):
        res = bc.check_recipient("926", "Alex 1001", "")
        self.assertFalse(res["allowed"])
        self.assertIn("could not resolve", res["reason"])

    def test_name_with_exactly_one_match_uses_resolved_id(self):
        res = bc.check_recipient(
            "Alex 1001", None, None,
            [{"chatID": "420", "title": "Alex 1001"}])
        self.assertTrue(res["allowed"], res)
        self.assertEqual(res["chatID"], "420")
        self.assertEqual(res["title"], "Alex 1001")

    def test_name_with_zero_matches_is_refused(self):
        res = bc.check_recipient("Alex 1001", None, None, [])
        self.assertFalse(res["allowed"])
        self.assertIn("no chat matches", res["reason"])

    def test_name_with_multiple_matches_lists_candidates(self):
        res = bc.check_recipient("Alex", None, None, [
            {"chatID": "420", "title": "Alex 1001 (WhatsApp)"},
            {"chatID": "77", "title": "Alex 1001 (Instagram)"},
        ])
        self.assertFalse(res["allowed"])
        self.assertIn("ambiguous", res["reason"])
        self.assertIn("420", res["reason"])
        self.assertIn("77", res["reason"])
        self.assertIn("WhatsApp", res["reason"])
        self.assertIn("Instagram", res["reason"])

    def test_name_match_with_wrong_expect_title_is_refused(self):
        res = bc.check_recipient(
            "Alex", "a contact", None,
            [{"chatID": "420", "title": "Alex 1001"}])
        self.assertFalse(res["allowed"])
        self.assertIn("a contact", res["reason"])
        self.assertIn("Alex 1001", res["reason"])
        self.assertIn("420", res["reason"])

    def test_title_compare_ignores_case_and_whitespace(self):
        res = bc.check_recipient("420", "  alex   1001 ", "Alex 1001")
        self.assertTrue(res["allowed"], res)

    def test_prefix_or_suffix_does_not_match(self):
        for expected, actual in (("Alex", "Alex 1001"),
                                 ("Alex 1001", "Alex"),
                                 ("1001", "Alex 1001")):
            res = bc.check_recipient("420", expected, actual)
            self.assertFalse(res["allowed"], (expected, actual, res))

    def test_normalize_title_collapses_whitespace_and_case(self):
        self.assertEqual(bc.normalize_title("  Alex \n\t 1001 "),
                         "alex 1001")


class CmdSendGateTest(unittest.TestCase):
    """cmd_send must consult the gate before it can transmit anything."""

    def _args(self, chat, expect_title=None):
        return SimpleNamespace(
            chat=chat, expect_title=expect_title, text="test", reply_to=None,
            dry_run=True, host="host", shim="shim", timeout=1.0,
            local_mode=False, state="unused",
        )

    def test_numeric_mismatch_fails_before_send_verified(self):
        args = self._args("926", "Alex 1001")
        with mock.patch.object(bc, "chat_title",
                               return_value="a contact"), \
                mock.patch.object(bc, "send_verified") as sent, \
                contextlib.redirect_stderr(io.StringIO()) as err, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit):
                bc.cmd_send(args)
        sent.assert_not_called()
        self.assertEqual(out.getvalue(), "")
        self.assertIn("Alex 1001", err.getvalue())
        self.assertIn("a contact", err.getvalue())

    def test_numeric_match_prints_recipient_and_sends_nothing_on_dry_run(self):
        args = self._args("420", "Alex 1001")
        with mock.patch.object(bc, "chat_title",
                               return_value="Alex 1001"), \
                mock.patch.object(bc, "send_verified") as sent, \
                contextlib.redirect_stderr(io.StringIO()) as err, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            bc.cmd_send(args)
        sent.assert_not_called()
        self.assertIn('recipient: "Alex 1001" (420)', err.getvalue())
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["arguments"]["chatID"], "420")

    def test_name_resolves_to_id_used_for_dry_run(self):
        args = self._args("Alex 1001")
        with mock.patch.object(bc, "resolve_chats",
                               return_value=[{"chatID": "420",
                                              "title": "Alex 1001"}]), \
                mock.patch.object(bc, "send_verified") as sent, \
                contextlib.redirect_stderr(io.StringIO()) as err, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            bc.cmd_send(args)
        sent.assert_not_called()
        self.assertIn('recipient: "Alex 1001" (420)', err.getvalue())
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["arguments"]["chatID"], "420")

    def test_name_ambiguity_refuses_before_send_verified(self):
        args = self._args("Alex")
        with mock.patch.object(bc, "resolve_chats", return_value=[
                {"chatID": "420", "title": "Alex 1001 (WhatsApp)"},
                {"chatID": "77", "title": "Alex 1001 (Instagram)"}]), \
                mock.patch.object(bc, "send_verified") as sent, \
                contextlib.redirect_stderr(io.StringIO()) as err, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit):
                bc.cmd_send(args)
        sent.assert_not_called()
        self.assertEqual(out.getvalue(), "")
        self.assertIn("ambiguous", err.getvalue())


if __name__ == "__main__":
    unittest.main()
