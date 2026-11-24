Option Explicit
Dim fso, shell, dir, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "cmd /c cd /d """ & dir & """ && uv run --with pystray --with pillow python """ & dir & "\caprouter_tray.py"""
shell.Run cmd, 0, False
