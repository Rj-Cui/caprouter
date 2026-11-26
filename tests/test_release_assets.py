from __future__ import annotations

import hashlib
import locale
import os
import re
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BOM = b"\xef\xbb\xbf"

# release.ps1 allowlist: these top-level files must appear verbatim in the zip
REQUIRED_FILES = (
    "install.ps1",
    "install.bat",
    "README.md",
    "pyproject.toml",
    "LICENSE",
    "CHANGELOG.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    ".mcp.json",
    # 0.7.3: the zip ships the DSH bundle package, so `tests/test_dsh_package.py`
    # (which is also in the zip) passes from an extracted release.
    "package.json",
    "cordis.patch.yml",
)

# These top-level directories must contribute at least one file to the zip
REQUIRED_PREFIXES = (
    "caprouter/",
    "bin/",
    "skills/",
    ".claude-plugin/",
    "schemas/",
    "docs/",
    "mods/",
    "commands/",
    "hooks/",
    "tray/",
    "packaging/",
    "examples/",
    # Documented in README: a released source zip can run the test suite and
    # `caprouter eval`, so both must ship (0.7.2 added them to the allowlist).
    "tests/",
    "eval/",
    # The bundle's display resources referenced from package.json.
    "locale/",
)

# Path segments that must never appear in the zip (build junk / dev dirs)
BANNED_SEGMENTS = (
    ".git",
    ".github",
    "build",
    "dist",
    "workspace",
    ".caprouter",
    "__pycache__",
)


def project_version() -> str:
    """Read the version from pyproject.toml (tests must not hardcode it)."""
    pyproject = ROOT / "pyproject.toml"
    raw = pyproject.read_bytes()
    try:
        import tomllib

        return str(tomllib.loads(raw.decode("utf-8"))["project"]["version"])
    except (ImportError, KeyError, UnicodeDecodeError, ValueError):
        match = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', raw.decode("utf-8", "replace"))
        if not match:
            raise AssertionError("could not parse [project] version from pyproject.toml")
        return match.group(1)


def powershell_exe() -> str | None:
    """Locate Windows PowerShell (PATH first, then the fixed System32 path)."""
    found = shutil.which("powershell")
    if found:
        return found
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    fallback = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if fallback.is_file():
        return str(fallback)
    return None


def decode_output(raw: bytes) -> str:
    """PowerShell 5.1 redirects output in the console code page; decode leniently."""
    encoding = locale.getpreferredencoding(False) or "utf-8"
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


class EntrypointEncodingTest(unittest.TestCase):
    """Encoding constraints for the entrypoint files and the packager (platform-independent)."""

    def test_install_ps1_is_utf8_with_bom(self) -> None:
        path = ROOT / "install.ps1"
        self.assertTrue(path.is_file(), "install.ps1 is missing")
        raw = path.read_bytes()
        self.assertGreater(len(raw), 3, "install.ps1 must not be empty")
        self.assertEqual(raw[:3], BOM, "install.ps1 must start with a UTF-8 BOM (239,187,191)")
        # Invalid UTF-8 would raise UnicodeDecodeError here
        raw[3:].decode("utf-8")

    def test_install_bat_is_ascii_with_crlf(self) -> None:
        path = ROOT / "install.bat"
        self.assertTrue(path.is_file(), "install.bat is missing")
        raw = path.read_bytes()
        self.assertTrue(raw, "install.bat must not be empty")
        self.assertFalse(
            any(byte > 0x7F for byte in raw),
            "install.bat must be pure ASCII, otherwise cmd renders it incorrectly",
        )
        text = raw.decode("ascii")
        lf_only = text.count("\n") - text.count("\r\n")
        self.assertEqual(lf_only, 0, "install.bat must use CRLF line endings")
        # A trailing line without newline is fine; isolated CR/LF is not
        self.assertNotIn("\r", text.replace("\r\n", ""), "install.bat must only use CRLF")

    def test_release_ps1_exists_with_bom(self) -> None:
        path = ROOT / "release.ps1"
        self.assertTrue(path.is_file(), "release.ps1 is missing")
        raw = path.read_bytes()
        self.assertGreater(len(raw), 3, "release.ps1 must not be empty")
        self.assertEqual(raw[:3], BOM, "release.ps1 must start with a UTF-8 BOM (239,187,191)")
        text = raw[3:].decode("utf-8")
        self.assertIn("==>", text, "release.ps1 should print ==> progress markers")


class ReleaseArtifactTest(unittest.TestCase):
    """Actually run release.ps1 and validate dist output and idempotency (Windows only)."""

    @classmethod
    def setUpClass(cls) -> None:
        if sys.platform != "win32":
            raise unittest.SkipTest("release.ps1 needs Windows PowerShell; skipped off Windows")
        cls.powershell = powershell_exe()
        if not cls.powershell:
            raise unittest.SkipTest("powershell.exe not found; cannot build the release")
        cls.version = project_version()
        cls.zip_name = f"caprouter-{cls.version}.zip"
        cls.dist = ROOT / "dist"

    def _remove_dist(self) -> None:
        shutil.rmtree(self.dist, ignore_errors=True)

    def _build(self) -> str:
        completed = subprocess.run(
            [
                self.powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "release.ps1"),
            ],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
        )
        output = decode_output(completed.stdout) + decode_output(completed.stderr)
        self.assertEqual(
            completed.returncode,
            0,
            f"release.ps1 failed (exit {completed.returncode}):\n{output}",
        )
        return output

    def test_release_script_builds_valid_archive(self) -> None:
        try:
            self._remove_dist()
            output = self._build()
            self.assertIn(self.zip_name, output, "release.ps1 should report the archive name")

            zip_path = self.dist / self.zip_name
            sha_path = self.dist / (self.zip_name + ".sha256")
            notes_path = self.dist / "RELEASE-NOTES.md"
            self.assertTrue(zip_path.is_file(), f"missing archive: {zip_path}")
            self.assertTrue(sha_path.is_file(), f"missing SHA256 file: {sha_path}")
            self.assertTrue(notes_path.is_file(), f"missing release notes: {notes_path}")

            zip_bytes = zip_path.read_bytes()
            digest = hashlib.sha256(zip_bytes).hexdigest()

            # .sha256 must be "lowercase hex + two spaces + file name"
            sha_text = sha_path.read_text(encoding="ascii")
            match = re.fullmatch(r"([0-9a-f]{64})  (\S+)\r?\n?", sha_text)
            self.assertIsNotNone(match, f"malformed .sha256: {sha_text!r}")
            assert match is not None
            self.assertEqual(match.group(1), digest, ".sha256 hash does not match the archive")
            self.assertEqual(match.group(2), self.zip_name, ".sha256 file name is wrong")

            with zipfile.ZipFile(zip_path) as archive:
                self.assertIsNone(archive.testzip(), "archive contains a corrupt entry")
                names = archive.namelist()
            self.assertGreater(len(names), 0, "archive must not be empty")
            self.assertEqual(len(names), len(set(names)), "archive contains duplicate paths")

            for filename in REQUIRED_FILES:
                self.assertIn(filename, names, f"archive is missing a required file: {filename}")
            for prefix in REQUIRED_PREFIXES:
                self.assertTrue(
                    any(name.startswith(prefix) for name in names),
                    f"archive is missing required directory content: {prefix}",
                )

            for name in names:
                with self.subTest(entry=name):
                    self.assertNotIn("\\", name, "zip paths must use forward slashes")
                    self.assertFalse(name.startswith("/"), "zip paths must not be absolute")
                    segments = name.split("/")
                    self.assertNotIn("..", segments, "zip paths must not contain ..")
                    for banned in BANNED_SEGMENTS:
                        self.assertNotIn(banned, segments, f"zip must not contain {banned}")
                    self.assertFalse(
                        name.endswith((".pyc", ".pyo")), "zip must not contain Python bytecode"
                    )
                    self.assertNotIn(".egg-info", name, "zip must not contain egg-info")

            zip_size = zip_path.stat().st_size
            notes = notes_path.read_text(encoding="utf-8-sig")
            self.assertIn(self.version, notes, "release notes should include the version")
            self.assertIn(f"{len(names)} file(s)", notes, "release notes should include the file count")
            self.assertIn(str(zip_size), notes, "release notes should include the archive size in bytes")
            self.assertIn(digest, notes, "release notes should include the SHA256")
            for marker in ("Extract All", "install.bat", "installation complete"):
                self.assertIn(marker, notes, f"release notes missing install hint: {marker}")
            for number in ("1", "2", "3"):
                self.assertRegex(
                    notes,
                    rf"(?m)^{number}\. \S",
                    f"release notes missing install step {number}",
                )
        finally:
            self._remove_dist()

    def test_release_script_rerun_is_idempotent(self) -> None:
        try:
            self._remove_dist()
            self._build()
            zip_path = self.dist / self.zip_name
            first_names: list[str]
            with zipfile.ZipFile(zip_path) as archive:
                first_names = archive.namelist()
            first_size = zip_path.stat().st_size

            # Rerun: bytes may change with content, but output must be overwritten cleanly
            self._build()
            produced = {item.name for item in self.dist.iterdir() if item.is_file()}
            self.assertEqual(
                produced,
                {self.zip_name, self.zip_name + ".sha256", "RELEASE-NOTES.md"},
                "a rerun must leave exactly the three release artifacts (no duplicates, no .tmp)",
            )

            sha_path = self.dist / (self.zip_name + ".sha256")
            sha_text = sha_path.read_text(encoding="ascii")
            self.assertEqual(len(sha_text.split("  ")[0]), 64, ".sha256 hash length is wrong")

            with zipfile.ZipFile(zip_path) as archive:
                self.assertIsNone(archive.testzip(), "archive is corrupt after the rerun")
                second_names = archive.namelist()
            self.assertEqual(
                set(first_names),
                set(second_names),
                "the archive file set changed between runs",
            )
            self.assertGreater(zip_path.stat().st_size, 0, "archive size is invalid")
            self.assertLess(
                abs(zip_path.stat().st_size - first_size),
                first_size,
                "archive size changed unexpectedly between runs",
            )
        finally:
            self._remove_dist()


if __name__ == "__main__":
    unittest.main()
