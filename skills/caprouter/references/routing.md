# CapRouter Routing Reference

The entry contract lives in `../SKILL.md`. This file is loaded only when routing details are needed.

## Pipeline

1. `caprouter scan` builds `registry.json` from local skills, plugins, CLI agents and project markers.
2. `caprouter route --need "..." --capability retrieve --domain research --top 5` returns top-k candidates.
3. `caprouter plan check --file plan.json` rejects brief/contract conflicts.
4. `caprouter run --plan plan.json` creates a run folder and a dispatch table (Phase 0: dry-run only).

## Filters

- `--capability`: retrieve / analyze / generate / transform / verify / format / design / operate_gui / decide / orchestrate.
- `--domain`: research / literature / office / design / engineering / media / writing / meta / general.
- `caprouter eval` measures top-1/top-k and context reduction. Dev cases are for tuning; held-out cases are for honest estimates.

## Defaults

- `min_score` is low (0.05); capability and domain filters do most of the pruning.
- Meta bundles (`meta/_bundles`) and old `demo-superagent-*` skills are excluded from routing by default.
- If nothing matches, the node should fall back to an available CLI agent rather than forcing a wrong skill.

## Rules of thumb

- One node, one output contract.
- Split retrieve and generate; do not ask one node to return URLs and a full report.
- Prefer a specialized skill when the domain is clear; otherwise let the CLI agent execute with its base capabilities.
