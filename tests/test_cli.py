from __future__ import annotations

import unittest
from unittest import mock

from caprouter.cli import _ask


class AskTest(unittest.TestCase):
    def test_non_tty_returns_default(self) -> None:
        stdin = mock.Mock()
        stdin.isatty.return_value = False
        with mock.patch("sys.stdin", stdin):
            self.assertEqual(_ask("question", "default", assume_yes=False), "default")

    def test_eof_returns_default(self) -> None:
        stdin = mock.Mock()
        stdin.isatty.return_value = True
        with mock.patch("sys.stdin", stdin), mock.patch("builtins.input", side_effect=EOFError):
            self.assertEqual(_ask("question", "default", assume_yes=False), "default")

    def test_empty_answer_returns_default(self) -> None:
        stdin = mock.Mock()
        stdin.isatty.return_value = True
        with mock.patch("sys.stdin", stdin), mock.patch("builtins.input", return_value="  "):
            self.assertEqual(_ask("question", "default", assume_yes=False), "default")

    def test_answer_wins(self) -> None:
        stdin = mock.Mock()
        stdin.isatty.return_value = True
        with mock.patch("sys.stdin", stdin), mock.patch("builtins.input", return_value="chosen"):
            self.assertEqual(_ask("question", "default", assume_yes=False), "chosen")


if __name__ == "__main__":
    unittest.main()
