@echo off
set "ROOT=%~dp0"
start "" /b uv run --with pystray --with pillow python "%ROOT%caprouter_tray.py"
