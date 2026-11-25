"""Host-skill linking must not follow a capability id out of the host root."""
from __future__ import annotations

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

from caprouter.hostaware import (  # noqa: E402
    link_managed_skill,
    safe_component,
    unlink_managed_skill,
)


class SafeComponentTests(unittest.TestCase):
    def test_rejects_traversal_and_absolute_names(self) -> None:
        for name in ("../x", "..\\x", "C:evil", "/etc/passwd", "", ".", "..", "a/b", "a\\b", "a\0b"):
            with self.subTest(name=name):
                self.assertFalse(safe_component(name))

    def test_accepts_normal_names(self) -> None:
        for name in ("pdf-tool", "skill_1", "中文技能", "a.b"):
            with self.subTest(name=name):
                self.assertTrue(safe_component(name))


class HostLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="caprouter-hostaware-"))
        enter_isolated_env(self, self.tmp / "home")
        self.workspace = make_workspace(self.tmp)
        self.host_root = self.tmp / "host-skills"
        self.host_root.mkdir()

    def _install_skill(self, name: str = "pdf-tool") -> Path:
        skill = self.workspace / ".caprouter" / "installed" / "skills" / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: demo\n---\nbody\n", encoding="utf-8"
        )
        return skill

    def test_link_copies_inside_the_host_root(self) -> None:
        self._install_skill()
        result = link_managed_skill(
            self.workspace, "skill:pdf-tool", host="claude-code", root=self.host_root
        )
        self.assertIsNotNone(result.get("linked"))
        linked = Path(result["linked"]).resolve()
        self.assertEqual(linked.parent, self.host_root.resolve())
        self.assertTrue((linked / "SKILL.md").is_file())

    def test_link_refuses_a_traversal_id(self) -> None:
        self._install_skill()
        victim = self.tmp / "victim"
        victim.mkdir()
        (victim / "keep.txt").write_text("keep", encoding="utf-8")
        result = link_managed_skill(
            self.workspace, "skill:../victim", host="claude-code", root=self.host_root, force=True
        )
        self.assertIsNone(result.get("linked"))
        self.assertIn("unsafe", str(result.get("skipped")).lower())
        self.assertTrue((victim / "keep.txt").is_file())

    def test_unlink_refuses_a_traversal_id(self) -> None:
        victim = self.tmp / "victim2"
        victim.mkdir()
        (victim / "keep.txt").write_text("keep", encoding="utf-8")
        result = unlink_managed_skill(
            self.workspace, "skill:../victim2", host="claude-code", root=self.host_root, force=True
        )
        self.assertFalse(result.get("removed"))
        self.assertTrue((victim / "keep.txt").is_file())

    def test_unlink_keeps_a_locally_edited_copy(self) -> None:
        source = self._install_skill()
        link_managed_skill(self.workspace, "skill:pdf-tool", host="claude-code", root=self.host_root)
        target = self.host_root / "pdf-tool" / "SKILL.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
        result = unlink_managed_skill(
            self.workspace,
            "skill:pdf-tool",
            host="claude-code",
            root=self.host_root,
            removed_path=source,
            force=False,
        )
        self.assertFalse(result.get("removed"))
        self.assertIn("differs", str(result.get("skipped")))


if __name__ == "__main__":
    unittest.main()
