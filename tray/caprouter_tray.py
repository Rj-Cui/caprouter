#!/usr/bin/env python3
"""Cross-platform CapRouter tray switch (pystray + Pillow)."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.backup import create_backup
from caprouter.state import read_state, state_path, write_state

LOCK_PORT = 47653


def make_icon(enabled: bool):
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = (46, 160, 67, 255) if enabled else (130, 130, 130, 255)
    draw.ellipse((4, 4, size - 4, size - 4), fill=color)
    draw.text((size // 2, size // 2), "C", fill=(255, 255, 255, 255), anchor="mm")
    return image


def _notify(icon, message: str) -> None:
    try:
        icon.notify(message, "CapRouter")
    except Exception:
        pass


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class TrayApp:
    def __init__(self, workspace: Path) -> None:
        import pystray

        self.workspace = workspace
        self.pystray = pystray
        enabled = bool(read_state().get("enabled", True))
        self.icon = pystray.Icon(
            "caprouter",
            make_icon(enabled),
            self._title(enabled),
            menu=pystray.Menu(
                pystray.MenuItem(self._toggle_text, self.toggle, default=True),
                pystray.MenuItem(self._status_text, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Backup config now", self.backup),
                pystray.MenuItem("Open state folder", self.open_folder),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self.exit),
            ),
        )

    @staticmethod
    def _title(enabled: bool) -> str:
        return "CapRouter: enabled" if enabled else "CapRouter: disabled"

    def _enabled(self) -> bool:
        return bool(read_state().get("enabled", True))

    def _toggle_text(self, item) -> str:
        return "Disable CapRouter" if self._enabled() else "Enable CapRouter"

    def _status_text(self, item) -> str:
        return "Status: ON" if self._enabled() else "Status: OFF"

    def _refresh(self) -> None:
        enabled = self._enabled()
        self.icon.icon = make_icon(enabled)
        self.icon.title = self._title(enabled)
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def toggle(self, icon=None, item=None) -> None:
        enabled = self._enabled()
        write_state(not enabled, "tray")
        self._refresh()
        _notify(self.icon, "CapRouter enabled" if not enabled else "CapRouter disabled")

    def backup(self, icon=None, item=None) -> None:
        try:
            result = create_backup(self.workspace)
            _notify(self.icon, f"Backup: {result['backup_dir']}")
        except Exception as exc:
            _notify(self.icon, f"Backup failed: {exc}")

    def open_folder(self, icon=None, item=None) -> None:
        _open_folder(state_path().parent)

    def exit(self, icon=None, item=None) -> None:
        self.icon.stop()

    def run(self) -> None:
        self.icon.run()


def _single_instance() -> socket.socket | None:
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
        lock.listen(1)
        return lock
    except OSError:
        try:
            lock.close()
        except OSError:
            pass
        return None


def resolve_workspace(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    env = os.environ.get("CAPROUTER_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    default = Path(__file__).resolve().parent.parent / "workspace"
    if default.is_dir():
        return default.resolve()
    return Path.cwd().resolve()


def self_test() -> int:
    old = os.environ.get("CAPROUTER_STATE")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CAPROUTER_STATE"] = str(Path(tmp) / "state.json")
        try:
            write_state(False, "selftest")
            if read_state()["enabled"]:
                print("selftest failed: expected disabled")
                return 1
            write_state(True, "selftest")
            if not read_state()["enabled"]:
                print("selftest failed: expected enabled")
                return 1
            print("SelfTest OK")
            return 0
        finally:
            if old is None:
                os.environ.pop("CAPROUTER_STATE", None)
            else:
                os.environ["CAPROUTER_STATE"] = old


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="caprouter-tray")
    parser.add_argument("--workspace")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--disable", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.status:
        print("enabled" if read_state().get("enabled", True) else "disabled")
        return 0
    if args.enable:
        write_state(True, "tray-cli")
        print("enabled")
        return 0
    if args.disable:
        write_state(False, "tray-cli")
        print("disabled")
        return 0

    lock = _single_instance()
    if lock is None:
        print("CapRouter tray is already running", file=sys.stderr)
        return 0

    try:
        TrayApp(resolve_workspace(args.workspace)).run()
    finally:
        try:
            lock.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
