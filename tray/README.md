# CapRouter Tray Switch

A cross-platform tray switch (pystray + Pillow) that controls the global
`enabled` state.

## Start

Windows (no console window):

```
Double-click <repo>\tray\start-tray.vbs
```

or:

```
<repo>\tray\start-tray.cmd
```

macOS / Linux:

```bash
./tray/start-tray.sh
```

On first run `uv` pulls `pystray` and `pillow`; it does not modify the global
Python environment.

## Menu

- Left click: toggle on/off (the default menu item);
- Right click: Enable/Disable, Status, Backup config now, Open state folder, Exit;
- Green icon = on, gray icon = off.

## State file

```
%USERPROFILE%\.caprouter\state.json          # Windows
~/.caprouter/state.json                      # macOS / Linux
```

```json
{"schema": "caprouter.state.v1", "enabled": true, "updated_at": "...", "updated_by": "tray"}
```

CapRouter's hook, CLI and skill all read this file. When it is off:

- `PreToolUse` lets all tools through;
- `UserPromptSubmit` does not inject CapRouter instructions;
- `caprouter run --execute` refuses to dispatch;
- `route` / `doctor` / `status` still work for read-only checks.

## Auto-use note

With the tray switch on and the Claude Code plugin loaded, the
`UserPromptSubmit` hook injects a CapRouter instruction for multi-step tasks.
**This is a reminder, not an enforcement**; the main agent can still decide it
does not apply. Taking over the input bar requires a Claude Code mod (see
`docs/mod-ui-plan.md`).

## Backup

The tray menu item "Backup config now" calls `caprouter backup`, which writes to:

```
<workspace>/backups/<timestamp>/
```

It contains `config.json`, `registry.json`, `state.json` and `plans/`. The CLI
works too:

```bash
python bin/caprouter.py backup
python bin/caprouter.py backups
python bin/caprouter.py restore --from <backup-dir> --force
```

## Autostart (Windows)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\tray\install-startup.ps1
# to remove:
powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\tray\install-startup.ps1 -Uninstall
```

For macOS/Linux autostart, use launchd/systemd; no script is provided yet.

## CLI fallback

```bash
uv run --with pystray --with pillow python tray/caprouter_tray.py --status
uv run --with pystray --with pillow python tray/caprouter_tray.py --disable
uv run --with pystray --with pillow python tray/caprouter_tray.py --enable
```
