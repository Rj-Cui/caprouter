---
description: (legacy orchestrator) Plan a task as capability nodes, validate the contracts, and dispatch it through CapRouter.
argument-hint: [task request]
---

> Compatibility path only. New work should use the `caprouter` capability bus
> (`capability_search` / `capability_invoke` / `capability_verify`).

Use the `caprouter` skill for this request.

1. Rewrite the request into a goal.
2. Decompose it into capability nodes with explicit output contracts and acceptance checks.
3. Write the plan to `.caprouter/plans/<task-id>.json`.
4. Run:

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/caprouter.py" plan check --file .caprouter/plans/<task-id>.json
python "${CLAUDE_PLUGIN_ROOT}/bin/caprouter.py" run --plan .caprouter/plans/<task-id>.json --dry-run
```

Do not execute the nodes yourself. If the plan has no unambiguous executor, ask the
user instead of falling back to native tools.

Request: $ARGUMENTS
