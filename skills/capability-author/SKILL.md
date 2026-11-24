---
name: capability-author
description: Create or refine a CapRouter skill/script after a real capability gap. Use when no verified local or trusted capability covers a task, when the user asks to turn a task trace into a skill, plugin, project script or capability card, or when a synthesized skill needs improvement from outcomes. Also trigger when the user has no existing tool, asks to create a skill/capability/script, asks to turn this task's flow into a skill, asks for post-task synthesis, to refine a candidate, or to improve a skill from outcomes. Do not use when an existing working tool already covers the task.
version: 0.1.0
keywords: create skill, create capability, synthesize skill, post-task synthesis, capability card, turn task into skill, skill authoring, synthesize, refine, improve
---

# Capability Author

Turn "there was no suitable tool this time" into "there is one next time". Record
the gap before writing the tool; propose a candidate before approving it.

## When to use

- `capability search` returns abstain, or top-1 is clearly mismatched;
- after a task, the user says "turn this workflow into a skill / script / plugin";
- a synthesized skill has failing outcomes and needs a patch;
- do not use it for ordinary tasks that already have a working tool.

## Hard rules

1. **Record the gap first**: without a `capability gap`, do not start synthesis.
2. **Candidates are not registered**: `synthesize propose` only writes
   `.caprouter/synthesized/`; only `approve` installs.
3. **Never auto-execute**: generated scripts are inert skeletons; no network, no
   subprocess, read-only by default.
4. **Never fabricate**: write only the steps, inputs, outputs, failures and
   acceptance that appear in the task trace.
5. **The Card must be reviewable**: description, examples, capabilities,
   permissions, invoke and probe are all required.
6. **Do not copy external code/assets**; record only sources and evidence.
7. **Auto-approval still goes through review**: `--auto` adds no new judgment
   rules; if review fails, roll back and never register silently.
8. **Improvements need approval too**: `improve` only generates a patch; only
   `--apply` replaces anything.

## Workflow

1. Confirm the gap: `caprouter capability gaps --workspace <ws>`.
2. Collect the task trace: goal, inputs, steps, outputs, acceptance, failures,
   corrections, artifacts.
3. Generate the candidate: `caprouter synthesize propose ... --trace <trace.json>`.
4. Refine the content (default `--method auto`):
   - when `skill:skill-creator` is available, use its principles + the CLI adapter;
   - when skill-creator is missing but a CLI adapter exists, use the adapter directly;
   - when no tool is available, fall back to `builtin`, template the
     workflow/checks, and do not block;
   - any adapter failure keeps the original candidate and falls back to builtin.
5. Have a human review the candidate `SKILL.md`, `card_draft.json`, permissions
   and scripts.
6. Approve: `caprouter synthesize approve --candidate <slug>`;
   for auto-approval use `--auto`: after install the built-in review checks the
   real Card; unless `auto_approved`, roll back the install/draft/registry and
   keep the candidate `proposed`; only `--apply` writes the overlay.
7. Verify: `capability get` → `capability probe` → real task `capability outcome`.
8. Improve: `caprouter synthesize improve --id skill:<slug>`; review the patch
   before `--apply`.

## Output format

The candidate must contain:

```text
skill/SKILL.md
skill/references/task-trace.md
skill/scripts/tool.py        # script only
card_draft.json
candidate.json
```

## Failure handling

- No gap: stop and record the gap first.
- Refinement failed: keep the original candidate, log the error, fall back to builtin.
- Review failed: do not approve; fix SKILL.md/Card and re-run.
- No outcome: do not claim "verified"; run a real task first.

See `references/authoring-checklist.md`.
