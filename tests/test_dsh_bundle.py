"""DSH bundle output and workspace resolution for host plugins."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _path in (ROOT, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from support import enter_isolated_env, make_workspace  # noqa: E402

from caprouter import __version__  # noqa: E402
from caprouter.mcp_server import resolve_workspace  # noqa: E402
from caprouter.setup_hosts import write_dsh_bundle  # noqa: E402


class ResolveWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="caprouter-resolve-"))
        self.home = self.tmp / "home"
        enter_isolated_env(self, self.home)

    def test_explicit_argument_wins(self) -> None:
        explicit = make_workspace(self.tmp)
        self.assertEqual(resolve_workspace(str(explicit)), explicit.resolve())

    def test_env_variable_is_second(self) -> None:
        import os

        self.addCleanup(os.environ.pop, "CAPROUTER_WORKSPACE", None)
        env_workspace = make_workspace(self.tmp / "env")
        os.environ["CAPROUTER_WORKSPACE"] = str(env_workspace)
        self.assertEqual(resolve_workspace(None), env_workspace.resolve())

    def test_default_install_location_is_used_when_initialized(self) -> None:
        # A host plugin starts the server without --workspace; the one-click
        # installer layout must be picked up so a fresh install works.
        default = self.home / "CapRouter" / "workspace"
        (default / ".caprouter").mkdir(parents=True)
        (default / ".caprouter" / "config.json").write_text("{}", encoding="utf-8")
        self.assertEqual(resolve_workspace(None), default.resolve())

    def test_falls_back_to_cwd_without_any_workspace(self) -> None:
        self.assertEqual(resolve_workspace(None), Path.cwd().resolve())


class DshBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="caprouter-dsh-bundle-"))
        enter_isolated_env(self, self.tmp / "home")
        self.workspace = make_workspace(self.tmp)
        self.bundle = self.tmp / "bundle"

    def _write(self) -> dict:
        return write_dsh_bundle(self.workspace, self.bundle)

    def test_manifest_carries_the_current_version(self) -> None:
        self._write()
        manifest = json.loads((self.bundle / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], __version__)
        self.assertIn("cordis.patch.yml", manifest["files"])
        self.assertIn("docs", manifest["files"])
        self.assertEqual(manifest["dsh"]["bundle"]["patch"], "./cordis.patch.yml")

    def test_locales_name_a_detail_document_that_exists(self) -> None:
        self._write()
        for locale in ("en", "zh"):
            data = json.loads((self.bundle / "locale" / f"{locale}.json").read_text(encoding="utf-8"))
            detail = data["meta"]["detail"]
            with self.subTest(locale=locale):
                self.assertTrue(data["meta"]["title"])
                self.assertTrue(data["meta"]["description"])
                self.assertTrue((self.bundle / detail).is_file(), detail)

    def test_detail_states_the_real_tool_profile_and_limits(self) -> None:
        self._write()
        text = (self.bundle / "docs" / "DETAIL.md").read_text(encoding="utf-8")
        self.assertIn("ten", text)
        self.assertIn("capability_search", text)
        self.assertIn("not published", text)
        self.assertIn("static", text)

    def test_patch_pins_the_core_profile_and_host(self) -> None:
        self._write()
        patch = (self.bundle / "cordis.patch.yml").read_text(encoding="utf-8")
        self.assertIn("serverName: caprouter", patch)
        self.assertIn("transport: stdio", patch)
        self.assertIn("--tools", patch)
        self.assertIn("core", patch)
        self.assertIn("deepseek-harness", patch)
        self.assertIn("failOnStartupError: true", patch)

    def test_icon_is_exported_when_the_repository_ships_one(self) -> None:
        repo_icon = ROOT / ".claude-plugin" / "icon.png"
        if not repo_icon.exists():
            self.skipTest("repository ships no icon")
        self._write()
        manifest = json.loads((self.bundle / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["exports"]["./icon"], "./icon.png")
        self.assertTrue((self.bundle / "icon.png").is_file())


class PortableBundleTests(unittest.TestCase):
    """The distributable bundle must not leak anything about this machine."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="caprouter-portable-"))
        enter_isolated_env(self, self.tmp / "home")
        self.workspace = make_workspace(self.tmp)
        self.bundle = self.tmp / "bundle"
        write_dsh_bundle(self.workspace, self.bundle, portable=True)
        self.text_files = [
            path
            for path in self.bundle.rglob("*")
            if path.is_file() and path.suffix in {".json", ".yml", ".md", ".yaml"}
        ]

    def test_no_absolute_paths_from_this_machine(self) -> None:
        forbidden = [
            str(Path.home()),
            str(self.workspace),
            str(ROOT),
            "python.exe",
            "C:\\",
            "D:\\",
        ]
        for path in self.text_files:
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                with self.subTest(file=path.name, needle=needle):
                    self.assertNotIn(needle, text, f"{path.name} leaks {needle!r}")

    def test_patch_runs_the_module_and_resolves_the_interpreter(self) -> None:
        patch = (self.bundle / "cordis.patch.yml").read_text(encoding="utf-8")
        self.assertIn("!!js", patch)
        self.assertIn("CAPROUTER_PYTHON", patch)
        self.assertIn("- -m", patch)
        self.assertIn("- caprouter", patch)
        self.assertNotIn("--workspace", patch)

    def test_portable_bundle_still_has_full_display_metadata(self) -> None:
        manifest = json.loads((self.bundle / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], __version__)
        for locale in ("en", "zh"):
            data = json.loads((self.bundle / "locale" / f"{locale}.json").read_text(encoding="utf-8"))
            self.assertTrue(data["meta"]["title"])
            self.assertTrue((self.bundle / data["meta"]["detail"]).is_file())
        detail = (self.bundle / "docs" / "DETAIL.md").read_text(encoding="utf-8")
        self.assertIn("pip install caprouter", detail)
        self.assertIn("CAPROUTER_WORKSPACE", detail)

    def test_local_mode_is_opt_in_and_keeps_working(self) -> None:
        local = self.tmp / "bundle-local"
        write_dsh_bundle(self.workspace, local)
        patch = (local / "cordis.patch.yml").read_text(encoding="utf-8")
        self.assertIn("--workspace", patch)
        self.assertNotIn("!!js", patch)


if __name__ == "__main__":
    unittest.main()
