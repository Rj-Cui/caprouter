# Single-file binary (optional)

CapRouter is dependency-free Python; a binary is a convenience, not a requirement.

```bash
python -m pip install pyinstaller
pyinstaller --onefile --name caprouter \
  --add-data "skills;skills" \
  --add-data "commands;commands" \
  --add-data "hooks;hooks" \
  --add-data ".mcp.json;." \
  --collect-submodules caprouter \
  bin/caprouter.py
```

Caveats:

- The binary still needs a workspace (`caprouter init`).
- Hooks/commands are host integrations; an MCP client can use the binary directly, but the
  Claude Code plugin form expects the repository layout.
- Build on each target OS; do not expect a Windows binary to run on macOS.
- Sign/notarize the binary before distributing it; an unsigned audit tool is a poor signal.
