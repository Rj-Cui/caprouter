---
description: Show or change the global CapRouter switch (enable/disable/status).
argument-hint: [enable|disable|status]
---

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/caprouter.py" state $ARGUMENTS
```

If no argument is given, it prints the current state and the state file path.
