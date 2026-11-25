"""Every registered command must parse, have a handler, and print help.

This is the cheap guard against the class of bug where a subparser is wired to a
missing function, or a group gains a command nobody can run.
"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _path in (ROOT, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from caprouter.cli import build_parser  # noqa: E402


def _subparsers_action(parser):
    for action in parser._actions:  # noqa: SLF001 - argparse introspection
        if hasattr(action, "choices") and str(action.dest).endswith("command"):
            return action
    return None


def iter_commands(parser, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], object]]:
    """Yield (argv path, parser) for every leaf command."""
    subparsers = _subparsers_action(parser)
    if subparsers is None:
        return [(path, parser)]
    leaves: list[tuple[tuple[str, ...], object]] = []
    for name, sub in subparsers.choices.items():
        if _subparsers_action(sub) is not None:
            leaves.extend(iter_commands(sub, (*path, name)))
        else:
            leaves.append(((*path, name), sub))
    return leaves


class CliSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parser = build_parser()
        cls.commands = iter_commands(cls.parser)

    def test_there_are_commands_to_check(self) -> None:
        self.assertGreaterEqual(len(self.commands), 60)

    def test_every_command_has_a_handler(self) -> None:
        missing = []
        for path, sub in self.commands:
            defaults = getattr(sub, "_defaults", {})
            if not callable(defaults.get("func")):
                missing.append(" ".join(path))
        self.assertEqual(missing, [])

    def test_every_command_prints_help(self) -> None:
        failures = []
        for path, _sub in self.commands:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                try:
                    self.parser.parse_args([*path, "--help"])
                except SystemExit as exc:
                    if exc.code not in (0, None) or not buffer.getvalue():
                        failures.append((" ".join(path), exc.code))
                except BaseException as exc:  # noqa: BLE001 - report, never hide
                    failures.append((" ".join(path), f"{type(exc).__name__}: {exc}"))
        self.assertEqual(failures, [])

    def test_grouped_commands_are_reachable(self) -> None:
        paths = {" ".join(path) for path, _ in self.commands}
        for expected in (
            "capability search",
            "capability get",
            "capability verify",
            "capability probe",
            "capability draft",
            "pipeline plan",
            "pipeline run",
            "synthesize propose",
            "migrate export",
            "deps plan",
            "quarantine list",
            "audit",
            "state",
            "doctor",
            "init",
            "bootstrap",
        ):
            with self.subTest(command=expected):
                self.assertIn(expected, paths)


if __name__ == "__main__":
    unittest.main()
