---
description: Review CapRouter Card drafts deterministically; optionally run semantic review and auto-apply low-risk cards.
argument-hint: [--id ID] [--semantic] [--agent qwen] [--auto-apply]
---

Run CapRouter's Card review pipeline:

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/caprouter.py" review --workspace "${CAPROUTER_WORKSPACE:-${CLAUDE_PLUGIN_ROOT:-.}/workspace}" $ARGUMENTS
```

Rules:

1. Start with deterministic review. Report the status counts and the top issues.
2. `--semantic` invokes a local CLI agent and can take tens of seconds per Card.
   Ask before running it on all drafts unless the user explicitly requested it.
3. `--auto-apply` only writes low-risk cards; medium cards need
   `review.allow_medium=true` plus a semantic pass; high/rejected never apply.
4. Never hand-edit an auto-approved Card without re-running review.
