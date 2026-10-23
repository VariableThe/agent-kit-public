"""Unit tests for send verification text comparison (stdlib unittest, no network).

``normalize_for_compare`` is called on two different provenances: the HTML
body an adapter stored, and the plain text we handed to ``send_message``.
Stripping HTML tags from both sides was asymmetric — the adapter renders
markdown to markup, so backticks disappeared on the stored side but survived
on ours, and literal ``< 7 >`` was eaten as if it were a tag on ours. The
``html`` flag keeps each side's normalization honest.
"""

import importlib.util
import os
import unittest

_SCRIPT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts",
    "beeper_collab.py"))

_spec = importlib.util.spec_from_file_location("beeper_collab", _SCRIPT)
bc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bc)

# Real payload: chat 420, message 70253. Body as handed to send_message, with
# the shared footer that send_verified appends before transmitting.
SENT_70253 = (
    "Not yet — the dataset is in OneDrive (CI-2026.zip, 68 MB). Need git LFS "
    "pull first, then run `python scripts/setup_datasets.py` to extract. Can "
    "do that when you're free." + bc.AI_DISCLOSURE
)

# The same message as Beeper stored it: markdown rendered to <code> (backticks
# gone), bare domain auto-linked, block tags instead of newlines.
STORED_70253 = (
    '<p>Not yet — the dataset is in OneDrive (<a href="http://CI-2026.zip" '
    'rel="noopener noreferrer" target="_blank">CI-2026.zip</a>, 68 MB). Need '
    'git LFS pull first, then run <code>python scripts/setup_datasets.py'
    '</code> to extract. Can do that when you\'re free.</p>\n'
    '<p>This message was sent by an assistant.</p>'
)


class RealPayloadRegressionTest(unittest.TestCase):
    def test_message_70253_stored_matches_sent(self):
        self.assertEqual(
            bc.normalize_for_compare(STORED_70253),
            bc.normalize_for_compare(SENT_70253, html=False))


class LiteralTextTest(unittest.TestCase):
    def test_angle_brackets_and_ampersands_match(self):
        self.assertEqual(
            bc.normalize_for_compare("5 &amp; 6 &lt; 7 &gt; 3"),
            bc.normalize_for_compare("5 & 6 < 7 > 3", html=False))

    def test_plain_side_keeps_literal_brackets(self):
        self.assertEqual(
            bc.normalize_for_compare("a <b> c", html=False), "a <b> c")


class MarkdownMarkerTest(unittest.TestCase):
    def test_paired_markers_match_rendered_markup(self):
        pairs = [
            ("`x`", "<code>x</code>"),
            ("*x*", "<strong>x</strong>"),
            ("_x_", "<em>x</em>"),
            ("~x~", "<del>x</del>"),
        ]
        for sent, stored in pairs:
            with self.subTest(sent=sent):
                self.assertEqual(
                    bc.normalize_for_compare(stored),
                    bc.normalize_for_compare(sent, html=False))

    def test_backticked_command_inside_sentence_matches(self):
        self.assertEqual(
            bc.normalize_for_compare(
                "run <code>python3 -m unittest discover</code> first"),
            bc.normalize_for_compare(
                "run `python3 -m unittest discover` first", html=False))

    def test_unpaired_markers_survive(self):
        for text in ("2 * 3", "snake_case", "a ~ b", "5 * 6 = 30", "a_b_c"):
            with self.subTest(text=text):
                self.assertEqual(
                    bc.normalize_for_compare(text, html=False), text)

    def test_marker_only_text_is_not_mangled(self):
        self.assertEqual(bc.normalize_for_compare("**", html=False), "**")
        self.assertEqual(bc.normalize_for_compare("`", html=False), "`")


class ProvenanceFlagTest(unittest.TestCase):
    def test_html_default_still_strips_tags(self):
        self.assertEqual(
            bc.normalize_for_compare("<p>one</p>\n<p>two</p>"), "one two")
        self.assertEqual(bc.normalize_for_compare("<b>hi</b> &amp; bye"),
                         "hi & bye")

    def test_plain_side_does_not_strip_tags(self):
        self.assertEqual(
            bc.normalize_for_compare("<p>one</p>", html=False), "<p>one</p>")

    def test_whitespace_collapsing_is_shared(self):
        self.assertEqual(
            bc.normalize_for_compare("  a \n\n b  ", html=False), "a b")

    def test_non_string_input_is_empty(self):
        self.assertEqual(bc.normalize_for_compare(None), "")
        self.assertEqual(bc.normalize_for_compare(None, html=False), "")

    def test_flag_is_keyword_only(self):
        with self.assertRaises(TypeError):
            bc.normalize_for_compare("x", False)


class NegativeCaseTest(unittest.TestCase):
    def test_changed_word_still_rejected(self):
        self.assertNotEqual(
            bc.normalize_for_compare(STORED_70253),
            bc.normalize_for_compare(
                SENT_70253.replace("OneDrive", "Dropbox"), html=False))

    def test_dropped_marker_content_still_rejected(self):
        self.assertNotEqual(
            bc.normalize_for_compare("<code>true</code>"),
            bc.normalize_for_compare("`false`", html=False))

    def test_extra_word_still_rejected(self):
        self.assertNotEqual(
            bc.normalize_for_compare("<p>done</p>"),
            bc.normalize_for_compare("done now", html=False))


if __name__ == "__main__":
    unittest.main()
