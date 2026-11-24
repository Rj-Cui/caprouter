---
description: First-load pipeline for CapRouter: scan non-system drives, merge the registry, draft Cards, and run static probes. Never auto-applies.
argument-hint: [--drive D | --project-root PATH] [--dry-run] [--no-probe]
---

Run the CapRouter bootstrap pipeline. It initializes the workspace if missing,
scans non-system fixed drives (or the explicit roots the user gave), merges
discovered projects into the registry, writes deterministic Card drafts, and
runs static probes. It does **not** apply drafts and does **not** execute
project code.

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/caprouter.py" bootstrap --workspace "${CAPROUTER_WORKSPACE:-${CLAUDE_PLUGIN_ROOT:-.}/workspace}" $ARGUMENTS
```

If the user did not provide a workspace, use `${CLAUDE_PLUGIN_ROOT}/workspace` or ask.
If the user only wants a dry run, pass `--dry-run`; it writes nothing.

After it finishes:

1. Report the scan counts, draft counts (`drafted` / `unchanged` / `needs_review`),
   and probe counts.
2. Point the user at `.caprouter/drafts/projects/`.
3. Do not run `capability draft --all --apply` unless the user explicitly asks.
