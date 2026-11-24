---
description: Export/import reviewed CapRouter Cards and probe/outcome evidence across machines.
argument-hint: export [--out PATH] | import --bundle PATH
---

Export:

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/caprouter.py" migrate export \
  --workspace "${CAPROUTER_WORKSPACE:-${CLAUDE_PLUGIN_ROOT:-.}/workspace}" $ARGUMENTS
```

Import on the new machine (workspace must already be initialized):

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/caprouter.py" migrate import \
  --workspace <new-workspace> --bundle <bundle.zip>
```

Rules:

1. Matching order: id → registry hash → manifest name → name → path hint.
2. Matched reviewed Cards are applied to the new overlay; unmatched Cards are
   written to `.caprouter/drafts/migrated/`.
3. Import does not run review. If the user wants local projects on the new
   machine to produce Cards without review, run `bootstrap --no-review`.
4. Do not delete unmatched drafts; report them instead.
