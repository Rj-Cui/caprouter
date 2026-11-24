---
description: Initialize CapRouter in a workspace: ask for workspace/delivery paths, scan local capabilities, build the registry.
argument-hint: [workspace path]
---

Run the CapRouter initializer. It asks for the workspace and delivery directory,
then scans local skills, plugins and CLI agents and writes
`.caprouter/config.json` and `.caprouter/registry.json`.

New workspaces default to `mode: bus` (the capability bus). Use
`--mode orchestrator` only for 0.2 compatibility.

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/caprouter.py" init
```

If the user provided a workspace path, pass it:

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/caprouter.py" init --workspace "$ARGUMENTS"
```

Do not scan or route manually; let the CLI do it.
